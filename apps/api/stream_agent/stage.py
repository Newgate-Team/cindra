"""The on-air stage: LiveAvatar sessions + the RTMP broadcaster +
overlays, behind the small Stage interface the runner drives.

A LiveAvatar session lasts at most 20 or 60 minutes (by plan), so a
24-hour stream is a chain of sessions. The next one is opened in the
background well before the current one runs out and takes over the
picture in a pause between lines -- the broadcaster keeps sending
frames the whole time, so viewers see one continuous stream. If a
session dies unexpectedly, the last frame holds while a replacement
starts.

The vendor SDKs are asyncio; the runner is plain threads. The stage
runs its own event loop in a background thread and the runner's calls
hop onto it.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Coroutine
from typing import Any

import aiohttp
import numpy as np

from stream_agent.broadcaster import Broadcaster
from stream_agent.liveavatar import SPEECH_RATE, LiveAvatarAuthError, LiveAvatarSession
from stream_agent.media import Layer, render_caption, render_reaction

log = logging.getLogger("stream_agent.stage")

# Start the next session this long before the current one's cap...
_HANDOVER_LEAD = 90.0
# ...and give up waiting for a quiet moment this close to the end.
_FORCE_HANDOVER_AT = 15.0
_MAX_SESSION_FAILURES = 5
_REACTION_SECONDS = 4.0
_CAPTION_TAIL_SECONDS = 2.0

SessionFactory = Callable[[aiohttp.ClientSession], LiveAvatarSession]


class AvatarStage:
    def __init__(
        self,
        job: dict[str, Any],
        *,
        rtmp_url: str,
        liveavatar_base_url: str,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        make_session: SessionFactory | None = None,
        handover_lead: float = _HANDOVER_LEAD,
    ) -> None:
        self.job = job
        self._width, self._height = width, height
        self._make_session = make_session or (
            lambda http: LiveAvatarSession(
                job["avatar_api_key"],
                job["avatar"]["avatar_id"],
                http=http,
                base_url=liveavatar_base_url,
            )
        )
        self._handover_lead = handover_lead
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="stage", daemon=True)
        self._http: aiohttp.ClientSession | None = None
        self.current: LiveAvatarSession | None = None
        self._next: LiveAvatarSession | None = None
        self._opening: asyncio.Task | None = None
        self._watch: asyncio.Future | None = None
        self.handovers = 0
        self.session_failures = 0
        self._retry_at = 0.0
        self._broken: str | None = None
        self._layers: dict[str, tuple[Layer, float]] = {}
        self._layers_lock = threading.Lock()
        self.broadcaster = Broadcaster(
            rtmp_url,
            picture=self._picture,
            audio=self._audio,
            layers=self._active_layers,
            width=width,
            height=height,
            fps=fps,
        )

    # -- thread hopping -----------------------------------------------------

    def _call[T](self, coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    # -- Stage interface ------------------------------------------------------

    def start(self) -> None:
        self._thread.start()
        try:
            self._call(self._start(), timeout=120)
        except BaseException:
            self.stop()
            raise
        self.broadcaster.start()

    async def _start(self) -> None:
        self._http = aiohttp.ClientSession()
        self.current = await self._open_session()
        self._watch = asyncio.ensure_future(self._watch_sessions())

    def speak(self, text: str, emotion: str, audio: bytes | None = None) -> None:
        if not audio or self.current is None:
            return
        seconds = len(audio) / (SPEECH_RATE * 2)
        self._extend_caption(seconds + _CAPTION_TAIL_SECONDS)
        session = self.current
        asyncio.run_coroutine_threadsafe(session.speak(audio), self._loop)

    def speaking(self) -> bool:
        return self.current is not None and self.current.speaking

    def healthy(self) -> bool:
        return self._broken is None and self.broadcaster.healthy()

    @property
    def problem(self) -> str | None:
        if self._broken:
            return self._broken
        if not self.broadcaster.healthy():
            return "Не удаётся вести трансляцию на Twitch — проверьте ключ трансляции"
        return None

    def show(self, *, author: str | None, message: str | None, reaction: str | None) -> None:
        now = time.monotonic()
        with self._layers_lock:
            if author and message:
                caption = render_caption(author, message, self._width, self._height)
                # Shown until the line is spoken; speak() sets the end.
                self._layers["caption"] = (caption, now + 60)
            else:
                self._layers.pop("caption", None)
            if reaction:
                layer = render_reaction(reaction, self._width, self._height)
                if layer is not None:
                    self._layers["reaction"] = (layer, now + _REACTION_SECONDS)

    def stop(self) -> None:
        self.broadcaster.stop()
        if self._thread.is_alive():
            try:
                self._call(self._shutdown(), timeout=30)
            except Exception:
                log.exception("stage shutdown failed")
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)

    async def _shutdown(self) -> None:
        if self._watch is not None:
            self._watch.cancel()
        if self._opening is not None:
            self._opening.cancel()
        for session in (self._next, self.current):
            if session is not None:
                await session.close()
        if self._http is not None:
            await self._http.close()

    # -- broadcaster sources --------------------------------------------------

    def _picture(self) -> np.ndarray | None:
        session = self.current
        return session.latest_frame if session is not None else None

    def _audio(self, samples: int) -> np.ndarray:
        session = self.current
        if session is None:
            return np.zeros(samples, dtype=np.int16)
        return session.audio.read(samples)

    def _active_layers(self) -> list[Layer]:
        now = time.monotonic()
        with self._layers_lock:
            self._layers = {k: v for k, v in self._layers.items() if v[1] > now}
            return [layer for layer, _ in self._layers.values()]

    def _extend_caption(self, seconds: float) -> None:
        with self._layers_lock:
            if "caption" in self._layers:
                layer, _ = self._layers["caption"]
                self._layers["caption"] = (layer, time.monotonic() + seconds)

    # -- session rotation -----------------------------------------------------

    async def _open_session(self) -> LiveAvatarSession:
        assert self._http is not None
        session = self._make_session(self._http)
        try:
            await session.open()
        except BaseException:
            await session.close()
            raise
        return session

    def _start_next(self) -> None:
        async def opener() -> None:
            try:
                self._next = await self._open_session()
                self.session_failures = 0
            except LiveAvatarAuthError as exc:
                self._broken = f"LiveAvatar не принял ключ: {exc}"
            except Exception as exc:
                self.session_failures += 1
                self._retry_at = time.monotonic() + min(30.0, 2.0**self.session_failures)
                log.warning(
                    "next avatar session failed (%s): %s", self.session_failures, exc, exc_info=True
                )
                if self.session_failures >= _MAX_SESSION_FAILURES:
                    self._broken = f"Аватар не запускается: {exc}"

        self._opening = asyncio.ensure_future(opener())

    async def _handover(self) -> None:
        old, self.current, self._next = self.current, self._next, None
        self.handovers += 1
        log.info("avatar handover #%s -> session %s", self.handovers, self.current.session_id)
        if old is not None:
            await old.close()

    async def _watch_sessions(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            current = self.current
            if current is None:
                continue
            left = current.max_duration - current.age() if current.max_duration else 1e9
            # A third of a short session (Free plan: 2 min) at most --
            # otherwise the next one would start almost immediately.
            lead = min(self._handover_lead, current.max_duration / 3 or self._handover_lead)
            needs_next = not current.alive or left <= lead
            opening = self._opening is not None and not self._opening.done()
            can_try = self._broken is None and time.monotonic() >= self._retry_at
            if needs_next and self._next is None and not opening and can_try:
                self._start_next()
            # Swap in a pause, unless the old session is about to be cut
            # off anyway (or already gone).
            quiet_moment = not current.alive or not current.speaking or left <= _FORCE_HANDOVER_AT
            if self._next is not None and self._next.ready and quiet_moment:
                await self._handover()
