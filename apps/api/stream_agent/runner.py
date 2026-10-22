"""The stream agent's main loop: claim a stream, keep it on air, wind it
down when told to (or when the API hands it to someone else).

Vendor-agnostic: everything visual/audible goes through a Stage (the
avatar session plus the encoder pushing RTMP). The runner decides what
to say and when; a Stage only has to speak, report whether it's still
speaking, and say whether it's healthy. An unhealthy Stage is rebuilt
in place -- for a 24h stream the avatar session *will* drop at some
point, and that must cost seconds, not the broadcast.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from stream_agent.api_client import ApiError, StreamApi, StreamLost, TemporaryApiError
from stream_agent.brain import ChatBrain, Pick
from stream_agent.twitch_chat import ChatReader

log = logging.getLogger("stream_agent")

HEARTBEAT_SECONDS = 20.0
_TICK = 0.2
# A stage that can't be rebuilt this many times in a row is broken
# (bad avatar key, vendor outage) -- end the stream with the reason
# rather than spin.
_MAX_STAGE_FAILURES = 5


class Stage(Protocol):
    def start(self) -> None: ...
    def speak(self, text: str, emotion: str) -> None: ...
    def speaking(self) -> bool: ...
    def healthy(self) -> bool: ...
    def show(self, *, author: str | None, message: str | None, reaction: str | None) -> None: ...
    def stop(self) -> None: ...


StageFactory = Callable[[dict[str, Any]], Stage]


@dataclass
class _Stats:
    chat_messages: int = 0
    replies: int = 0
    skipped: int = 0
    stage_restarts: int = 0
    chat_reconnects: int = 0
    started: float = field(default_factory=time.monotonic)

    def as_dict(self) -> dict[str, Any]:
        return {
            "chat_messages": self.chat_messages,
            "replies": self.replies,
            "skipped": self.skipped,
            "stage_restarts": self.stage_restarts,
            "chat_reconnects": self.chat_reconnects,
            "uptime_seconds": int(time.monotonic() - self.started),
        }


class StreamSession:
    """One claimed stream, from going live to finish()."""

    def __init__(
        self,
        api: StreamApi,
        job: dict[str, Any],
        make_stage: StageFactory,
        *,
        chat: ChatReader | None = None,
        brain: ChatBrain | None = None,
        clock: Callable[[], float] = time.monotonic,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
    ) -> None:
        self.api = api
        self.job = job
        self.id = str(job["id"])
        self._make_stage = make_stage
        self.chat = chat or ChatReader(job["channel"])
        self.brain = brain or ChatBrain(clock=clock)
        self._clock = clock
        self._heartbeat_seconds = heartbeat_seconds
        self.min_interval = float(job["min_reply_interval_seconds"])
        self.stats = _Stats()
        self.stop_reason: str | None = None
        self.lost = False
        self._stop = threading.Event()
        self._stage: Stage | None = None
        self._was_speaking = False
        self._last_reply_at = -1e9
        self._last_beat = -1e9

    # -- lifecycle ------------------------------------------------------

    def request_stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        error: str | None = None
        try:
            self.chat.start()
            self._start_stage()
            self._report("system", "В эфире")
            while not self._stop.is_set():
                self._tick()
                self._stop.wait(_TICK)
        except StreamLost:
            self.lost = True
            log.warning("stream %s was handed to another agent", self.id)
        except _StageBroken as exc:
            error = str(exc)
        except Exception as exc:
            log.exception("stream %s crashed", self.id)
            error = f"Сбой агента эфира: {exc}"
        finally:
            if self._stage is not None:
                try:
                    self._stage.stop()
                except Exception:
                    log.exception("stage stop failed")
            self.chat.stop()
            if not self.lost:
                try:
                    self.api.finish(self.id, error)
                except (ApiError, StreamLost):
                    log.exception("could not report the end of stream %s", self.id)

    def _start_stage(self) -> None:
        failures = 0
        while True:
            stage = self._make_stage(self.job)
            try:
                stage.start()
                self._stage = stage
                return
            except Exception as exc:
                failures += 1
                log.warning("stage start failed (%s/%s): %s", failures, _MAX_STAGE_FAILURES, exc)
                try:
                    stage.stop()
                except Exception:
                    log.exception("stopping a failed stage failed too")
                if failures >= _MAX_STAGE_FAILURES:
                    raise _StageBroken(f"Аватар не запускается: {exc}") from exc
                if self._stop.wait(min(30.0, 2.0**failures)):
                    raise _StageBroken("Эфир остановлен до запуска аватара") from exc

    # -- the loop ---------------------------------------------------------

    def _tick(self) -> None:
        now = self._clock()
        if now - self._last_beat >= self._heartbeat_seconds:
            self._heartbeat(now)
            if self._stop.is_set():
                return

        while True:
            try:
                event = self.chat.events.get_nowait()
            except queue.Empty:
                break
            if event.kind == "message":
                self.stats.chat_messages += 1
            self.brain.observe(event)
        self.stats.chat_reconnects = self.chat.reconnects

        stage = self._stage
        if stage is None:
            return
        if not stage.healthy():
            self.stats.stage_restarts += 1
            self._report("system", "Аватар переподключается")
            try:
                stage.stop()
            except Exception:
                log.exception("stopping the dead stage failed")
            self._stage = None
            self._start_stage()
            return

        speaking = stage.speaking()
        if self._was_speaking and not speaking:
            self.brain.spoke()
            self._last_reply_at = now
        self._was_speaking = speaking
        if speaking or now - self._last_reply_at < self.min_interval:
            return

        pick = self.brain.next_pick()
        if pick is not None:
            self._say(pick, stage)

    def _heartbeat(self, now: float) -> None:
        self._last_beat = now
        try:
            answer = self.api.heartbeat(self.id, self.stats.as_dict())
        except TemporaryApiError as exc:
            log.warning("heartbeat failed: %s", exc)
            return
        self.min_interval = float(answer.get("min_reply_interval_seconds", self.min_interval))
        if answer.get("stop"):
            self.stop_reason = answer.get("stop_reason")
            log.info("stream %s stopping: %s", self.id, self.stop_reason)
            self._stop.set()

    def _say(self, pick: Pick, stage: Stage) -> None:
        recent = [
            {"author": e.display_name or e.user, "text": e.text[:500]}
            for e in self.brain.recent_chat()
        ]
        kwargs: dict[str, Any] = {"recent_chat": recent}
        event = pick.event
        if pick.mode == "answer" and event is not None:
            kwargs["target"] = {"author": event.display_name or event.user, "text": event.text[:500]}
        elif pick.mode == "notice" and event is not None:
            kwargs["notice"] = {
                "type": event.notice_type,
                "author": event.display_name or event.user,
                "viewer_count": event.viewer_count,
                "text": event.text[:500],
            }
        try:
            reply = self.api.reply(self.id, pick.mode, **kwargs)
        except TemporaryApiError as exc:
            log.warning("reply failed, will pick again: %s", exc)
            self._last_reply_at = self._clock()  # don't hammer a failing API
            return
        except ApiError as exc:
            log.warning("reply refused: %s", exc)
            self._last_reply_at = self._clock()
            return
        if not reply.get("say"):
            self.stats.skipped += 1
            return
        stage.show(
            author=kwargs.get("target", kwargs.get("notice", {})).get("author"),
            message=kwargs.get("target", {}).get("text"),
            reaction=reply.get("reaction"),
        )
        stage.speak(reply["say"], reply.get("emotion") or "neutral")
        self._was_speaking = True
        self.stats.replies += 1

    def _report(self, kind: str, text: str) -> None:
        try:
            self.api.event(self.id, kind, text)
        except TemporaryApiError:
            pass


class _StageBroken(Exception):
    pass


def run(
    api: StreamApi,
    make_stage: StageFactory,
    *,
    once: bool = False,
    idle_seconds: float = 10.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Claim streams one after another. One agent process = one stream
    at a time: encoding a 1080p stream is a machine's worth of work."""
    while True:
        try:
            job = api.claim()
        except TemporaryApiError as exc:
            log.warning("claim failed: %s", exc)
            job = None
        if job is not None:
            log.info("going live: %s (#%s)", job["title"], job["channel"])
            StreamSession(api, job, make_stage).run()
        if once:
            return
        if job is None:
            sleep(idle_seconds)
