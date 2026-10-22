"""Watching an avatar in a LiveKit room: the part every avatar vendor
shares (LiveAvatar and Simli both stream their avatar into LiveKit).

Joins as a viewer, keeps the newest video frame and buffers the audio
at 48 kHz for the broadcaster, and reports the end when the room or the
avatar goes away.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import numpy as np
from livekit import rtc

from stream_agent.media import AudioRing

log = logging.getLogger("stream_agent.livekit")

FIRST_FRAME_TIMEOUT = 45.0


class LiveKitFeed:
    def __init__(self, on_end: Callable[[str], None]) -> None:
        self.audio = AudioRing()
        self.latest_frame: np.ndarray | None = None
        self.frame_at = 0.0
        self._first_frame = asyncio.Event()
        self._on_end = on_end
        self._room = rtc.Room()
        self._tasks: list[asyncio.Task] = []

    @property
    def has_picture(self) -> bool:
        return self._first_frame.is_set()

    async def join(self, url: str, token: str, timeout: float = FIRST_FRAME_TIMEOUT) -> None:
        self._room.on("track_subscribed", self._on_track)
        self._room.on("disconnected", lambda *_: self._on_end("room disconnected"))
        self._room.on("participant_disconnected", lambda *_: self._on_end("avatar left the room"))
        await self._room.connect(url, token)
        await asyncio.wait_for(self._first_frame.wait(), timeout=timeout)

    async def leave(self) -> None:
        for task in self._tasks:
            task.cancel()
        try:
            await self._room.disconnect()
        except Exception:  # already gone
            log.debug("room disconnect failed", exc_info=True)

    def _on_track(self, track: rtc.Track, *_: Any) -> None:
        if track.kind == rtc.TrackKind.KIND_VIDEO:
            self._tasks.append(asyncio.create_task(self._read_video(track)))
        elif track.kind == rtc.TrackKind.KIND_AUDIO:
            self._tasks.append(asyncio.create_task(self._read_audio(track)))

    async def _read_video(self, track: rtc.Track) -> None:
        async for event in rtc.VideoStream(track):
            frame = event.frame.convert(rtc.VideoBufferType.RGBA)
            # Copied: the SDK reuses the frame's buffer once we move on.
            self.latest_frame = np.frombuffer(frame.data, dtype=np.uint8).reshape(
                frame.height, frame.width, 4
            ).copy()
            self.frame_at = time.monotonic()
            self._first_frame.set()

    async def _read_audio(self, track: rtc.Track) -> None:
        async for event in rtc.AudioStream(track, sample_rate=48000, num_channels=1):
            self.audio.write(bytes(event.frame.data))
