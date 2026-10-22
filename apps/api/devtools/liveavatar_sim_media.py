"""The simulated avatar itself, for devtools/liveavatar_simulator.py
running in media mode (LIVEAVATAR_SIM_LIVEKIT_URL set, e.g. a local
`livekit-server --dev`).

On session start a fake avatar joins a LiveKit room and publishes what
LiveAvatar would: a video track (a cartoon face whose mouth follows the
audio level, on a background colour that changes per session so a
handover is visible in a recording) and an audio track that plays back
exactly the speech the agent sent over the session WebSocket. It ends
itself at max_session_duration or after 5 idle minutes, like the real
service.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from stream_agent.media import _font, resample_24k_to_48k

log = logging.getLogger("liveavatar_sim")

WIDTH, HEIGHT, FPS = 640, 360, 25
_RATE = 48000
_SAMPLES_10MS = _RATE // 100
_IDLE_TIMEOUT = float(os.environ.get("LIVEAVATAR_SIM_IDLE_TIMEOUT", "300"))
_COLOURS = [(40, 70, 120), (110, 50, 80), (40, 100, 70), (120, 90, 30)]
_MAX_FRAME = 1024 * 1024


def livekit_config() -> tuple[str, str, str] | None:
    url = os.environ.get("LIVEAVATAR_SIM_LIVEKIT_URL")
    if not url:
        return None
    return (
        url,
        os.environ.get("LIVEAVATAR_SIM_LIVEKIT_KEY", "devkey"),
        os.environ.get("LIVEAVATAR_SIM_LIVEKIT_SECRET", "secret"),
    )


def mint(identity: str, room: str, *, publish: bool) -> str:
    from livekit import api

    _, key, secret = livekit_config()
    grants = api.VideoGrants(
        room_join=True, room=room, can_publish=publish, can_subscribe=not publish
    )
    return api.AccessToken(key, secret).with_identity(identity).with_grants(grants).to_jwt()


@dataclass
class SimAvatar:
    session_id: str
    number: int
    max_duration: float
    room_name: str
    audio: bytearray = field(default_factory=bytearray)
    level: float = 0.0
    utterance: str | None = None
    committed: bool = False
    talking: bool = False
    last_active: float = field(default_factory=time.monotonic)
    ended: str | None = None
    ws: Any = None
    _tasks: list[asyncio.Task] = field(default_factory=list)

    # -- websocket events -------------------------------------------------

    async def emit(self, kind: str, **payload: Any) -> None:
        if self.ws is None:
            return
        event = {"type": kind, "event_id": os.urandom(16).hex(), "source_event_id": None, **payload}
        with contextlib.suppress(Exception):
            await self.ws.send_text(json.dumps(event))

    async def on_command(self, raw: str) -> None:
        if len(raw) > _MAX_FRAME:
            await self.emit("error", error={"type": "invalid_request_error",
                                            "message": "frame over 1 MB", "event_id": None})
            return
        try:
            command = json.loads(raw)
        except ValueError:
            await self.emit("error", error={"type": "invalid_request_error",
                                            "message": "malformed JSON", "event_id": None})
            return
        kind, event_id = command.get("type"), command.get("event_id")
        self.last_active = time.monotonic()
        if kind == "agent.speak":
            try:
                pcm = base64.b64decode(command["audio"], validate=True)
            except (KeyError, ValueError):
                await self.emit("error", source_event_id=event_id,
                                error={"type": "invalid_request_error",
                                       "message": "missing or invalid audio", "event_id": event_id})
                return
            if self.utterance is None:
                self.utterance, self.committed = event_id or os.urandom(16).hex(), False
            self.audio += resample_24k_to_48k(pcm)
            await self.emit("agent.audio_buffer_appended", source_event_id=self.utterance)
        elif kind == "agent.speak_end":
            if command.get("audio"):
                self.audio += resample_24k_to_48k(base64.b64decode(command["audio"]))
            self.committed = True
            await self.emit("agent.audio_buffer_committed", source_event_id=self.utterance)
        elif kind == "agent.interrupt":
            self.audio.clear()
            if self.utterance is not None:
                await self.emit("agent.speak_interrupted", source_event_id=self.utterance)
            self.utterance, self.committed = None, False
            await self.emit("agent.audio_buffer_cleared", source_event_id=event_id)
        elif kind in ("session.keep_alive", "agent.start_listening", "agent.stop_listening"):
            pass
        else:
            await self.emit("error", source_event_id=event_id,
                            error={"type": "invalid_request_error",
                                   "message": f"unknown type {kind!r}", "event_id": event_id})

    # -- media --------------------------------------------------------------

    def _face(self) -> np.ndarray:
        image = Image.new("RGBA", (WIDTH, HEIGHT), _COLOURS[self.number % len(_COLOURS)] + (255,))
        draw = ImageDraw.Draw(image)
        cx, cy = WIDTH // 2, HEIGHT // 2 - 10
        draw.ellipse((cx - 90, cy - 110, cx + 90, cy + 110), fill=(232, 190, 160, 255))
        for dx in (-35, 35):
            draw.ellipse((cx + dx - 10, cy - 40, cx + dx + 10, cy - 20), fill=(40, 40, 50, 255))
        draw.text((12, 10), f"SIM AVATAR · сессия #{self.number}", font=_font(16),
                  fill=(255, 255, 255, 255))
        return np.asarray(image).copy()

    async def run_protocol_only(self) -> None:
        """No LiveKit: the WebSocket protocol and its timing only (speech
        "plays" in real time, events fire), for tests of the control
        channel."""
        task = asyncio.create_task(self._audio_loop(None))
        started = time.monotonic()
        try:
            while self.ended is None:
                await asyncio.sleep(0.1)
                if time.monotonic() - started >= self.max_duration:
                    self.ended = "MAX_DURATION_REACHED"
        finally:
            task.cancel()
            await self.emit("session.state_updated", state="disconnected")
            with contextlib.suppress(Exception):
                await self.ws.close()

    async def run(self, url: str) -> None:
        from livekit import rtc

        room = rtc.Room()
        await room.connect(url, mint(f"avatar-{self.session_id[:8]}", self.room_name, publish=True))
        video = rtc.VideoSource(WIDTH, HEIGHT)
        # A short queue keeps the mouth (driven from this loop) in step
        # with what viewers actually hear.
        sound = rtc.AudioSource(_RATE, 1, queue_size_ms=100)
        await room.local_participant.publish_track(
            rtc.LocalVideoTrack.create_video_track("avatar-video", video),
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA),
        )
        await room.local_participant.publish_track(
            rtc.LocalAudioTrack.create_audio_track("avatar-audio", sound),
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
        )
        await self.emit("session.state_updated", state="connected")
        self._tasks = [
            asyncio.create_task(self._video_loop(video)),
            asyncio.create_task(self._audio_loop(sound)),
        ]
        started = time.monotonic()
        try:
            while self.ended is None:
                await asyncio.sleep(0.5)
                if time.monotonic() - started >= self.max_duration:
                    self.ended = "MAX_DURATION_REACHED"
                elif time.monotonic() - self.last_active >= _IDLE_TIMEOUT:
                    self.ended = "IDLE_TIMEOUT"
        finally:
            for task in self._tasks:
                task.cancel()
            log.info("sim avatar %s ended: %s", self.session_id, self.ended)
            await self.emit("session.state_updated", state="disconnected")
            with contextlib.suppress(Exception):
                await self.ws.close()
            await room.disconnect()

    async def _video_loop(self, source: Any) -> None:
        from livekit import rtc

        face = self._face()
        cx, cy = WIDTH // 2, HEIGHT // 2 + 50
        next_at = time.monotonic()
        while True:
            frame = face.copy()
            opening = int(4 + self.level * 40)
            y0, y1 = cy - opening // 2, cy + opening // 2 + 1
            frame[y0:y1, cx - 30 : cx + 30, :3] = (120, 30, 40)
            source.capture_frame(rtc.VideoFrame(WIDTH, HEIGHT, rtc.VideoBufferType.RGBA,
                                                frame.tobytes()))
            next_at += 1 / FPS
            await asyncio.sleep(max(0.0, next_at - time.monotonic()))

    async def _audio_loop(self, source: Any) -> None:
        chunk_bytes = _SAMPLES_10MS * 2
        while True:
            if len(self.audio) >= chunk_bytes or (self.committed and self.audio):
                chunk = bytes(self.audio[:chunk_bytes]).ljust(chunk_bytes, b"\0")
                del self.audio[:chunk_bytes]
                samples = np.frombuffer(chunk, dtype=np.int16)
                self.level = min(1.0, float(np.abs(samples).mean()) / 4000)
                if not self.talking and self.utterance:
                    self.talking = True
                    await self.emit("agent.speak_started", source_event_id=self.utterance)
                    await self.emit("agent.state_updated", previous_state="idle",
                                    new_state="talking", source_event_id=self.utterance)
            else:
                chunk = b"\0" * chunk_bytes
                self.level = 0.0
                if self.talking and self.committed:
                    self.talking = False
                    finished, self.utterance, self.committed = self.utterance, None, False
                    await self.emit("agent.speak_ended", source_event_id=finished)
                    await self.emit("agent.state_updated", previous_state="talking",
                                    new_state="idle")
            if source is None:
                await asyncio.sleep(0.01)
                continue
            from livekit import rtc

            # capture_frame paces itself against the source's queue.
            await source.capture_frame(rtc.AudioFrame(chunk, _RATE, 1, _SAMPLES_10MS))
