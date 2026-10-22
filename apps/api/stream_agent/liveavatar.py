"""One HeyGen LiveAvatar session in LITE mode, from the agent's side.

LITE mode: Cindra supplies the speech (PCM16, 24 kHz, sent over the
session's WebSocket), LiveAvatar animates the face and streams the
result into a LiveKit room, which this process joins as a viewer to
pick up the avatar's video and audio for the broadcast.

Protocol per docs.liveavatar.com (LITE mode events/lifecycle) and the
official livekit-plugins-liveavatar: POST /v1/sessions/token (X-API-KEY)
-> POST /v1/sessions/start (Bearer session_token) -> WebSocket ws_url
({"type": "agent.speak", "event_id", "audio"} in ~1 s chunks, then
"agent.speak_end"; "session.keep_alive" against the 5-minute idle
timeout) -> POST /v1/sessions/stop.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Any

import aiohttp
import numpy as np
from livekit import rtc

from stream_agent.media import AudioRing

log = logging.getLogger("stream_agent.liveavatar")

DEFAULT_BASE_URL = "https://api.liveavatar.com"
SPEECH_RATE = 24000
_CHUNK_BYTES = SPEECH_RATE * 2  # 1 s of PCM16 mono, as the docs recommend
# Longest first: a session that lasts longer means fewer handovers. The
# API refuses anything above the account's plan cap (Business 60 min,
# Essential 20 min, Free 2 min), so step down until one is accepted.
_DURATIONS = (3600, 1200, 120)
_KEEP_ALIVE_SECONDS = 60.0
_FIRST_FRAME_TIMEOUT = 45.0


class LiveAvatarError(Exception):
    pass


class LiveAvatarAuthError(LiveAvatarError):
    pass


class LiveAvatarSession:
    def __init__(
        self,
        api_key: str,
        avatar_id: str,
        *,
        http: aiohttp.ClientSession,
        base_url: str = DEFAULT_BASE_URL,
        quality: str = "high",
    ) -> None:
        self._api_key = api_key
        self._avatar_id = avatar_id
        self._http = http
        self._base = base_url.rstrip("/")
        self._quality = quality
        self.session_id: str | None = None
        self._session_token: str | None = None
        self.max_duration = 0
        self.opened_at = 0.0
        self.audio = AudioRing()
        self.latest_frame: np.ndarray | None = None
        self.frame_at = 0.0
        self.ended: str | None = None
        self._speaking_until = 0.0
        self._utterance: str | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ws_connected = asyncio.Event()
        self._first_frame = asyncio.Event()
        self._room = rtc.Room()
        self._tasks: list[asyncio.Task] = []

    # -- REST -------------------------------------------------------------

    async def _post(self, path: str, body: dict[str, Any] | None, headers: dict[str, str]):
        async with self._http.post(f"{self._base}{path}", json=body, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=30)) as response:
            text = await response.text()
            if response.status in (401, 403):
                raise LiveAvatarAuthError(f"LiveAvatar отклонил ключ ({response.status})")
            if response.status >= 400:
                raise LiveAvatarError(f"LiveAvatar {response.status}: {text[:300]}")
            payload = json.loads(text) if text else {}
            return payload.get("data") if isinstance(payload, dict) else None

    async def _create_token(self) -> dict[str, Any]:
        last: Exception | None = None
        for duration in _DURATIONS:
            body = {
                "mode": "LITE",
                "avatar_id": self._avatar_id,
                "video_settings": {"quality": self._quality, "encoding": "H264"},
                "max_session_duration": duration,
            }
            try:
                data = await self._post("/v1/sessions/token", body, {"X-API-KEY": self._api_key})
            except LiveAvatarAuthError:
                raise
            except LiveAvatarError as exc:
                last = exc  # most likely above this plan's cap: try shorter
                continue
            self.max_duration = duration
            return data
        raise LiveAvatarError(f"LiveAvatar не создал сессию: {last}")

    # -- lifecycle --------------------------------------------------------

    async def open(self) -> None:
        started = await self.start_session()
        await self.connect_control(started["ws_url"])
        await self.join_room(started["livekit_url"], started["livekit_client_token"])
        log.info("LiveAvatar session %s live (max %ss)", self.session_id, self.max_duration)

    async def start_session(self) -> dict[str, Any]:
        token = await self._create_token()
        self.session_id, self._session_token = token["session_id"], token["session_token"]
        started = await self._post(
            "/v1/sessions/start", None, {"Authorization": f"Bearer {self._session_token}"}
        )
        self.max_duration = int(started.get("max_session_duration") or self.max_duration)
        self.opened_at = time.monotonic()
        return started

    async def connect_control(self, ws_url: str) -> None:
        self._ws = await self._http.ws_connect(ws_url, heartbeat=30)
        self._tasks.append(asyncio.create_task(self._read_ws(), name="liveavatar-ws"))
        await asyncio.wait_for(self._ws_connected.wait(), timeout=20)
        self._tasks.append(asyncio.create_task(self._keep_alive(), name="liveavatar-keepalive"))

    async def join_room(self, url: str, token: str) -> None:
        self._room.on("track_subscribed", self._on_track)
        self._room.on("disconnected", lambda *_: self._end("room disconnected"))
        self._room.on("participant_disconnected", lambda *_: self._end("avatar left the room"))
        await self._room.connect(url, token)
        await asyncio.wait_for(self._first_frame.wait(), timeout=_FIRST_FRAME_TIMEOUT)

    async def close(self, reason: str = "USER_CLOSED") -> None:
        self._end(reason)
        for task in self._tasks:
            task.cancel()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        try:
            await self._room.disconnect()
        except Exception:  # already gone
            log.debug("room disconnect failed", exc_info=True)
        if self._session_token:
            try:
                await self._post(
                    "/v1/sessions/stop",
                    {"session_id": self.session_id, "reason": "USER_CLOSED"},
                    {"Authorization": f"Bearer {self._session_token}"},
                )
            except (LiveAvatarError, aiohttp.ClientError, TimeoutError) as exc:
                # The vendor reaps abandoned sessions; credits stop with it.
                log.warning("stopping LiveAvatar session %s failed: %s", self.session_id, exc)

    def _end(self, reason: str) -> None:
        if self.ended is None:
            self.ended = reason
            log.info("LiveAvatar session %s ended: %s", self.session_id, reason)

    @property
    def alive(self) -> bool:
        return self.ended is None

    @property
    def ready(self) -> bool:
        return self.alive and self._first_frame.is_set()

    def age(self) -> float:
        return time.monotonic() - self.opened_at if self.opened_at else 0.0

    # -- speech -----------------------------------------------------------

    @property
    def speaking(self) -> bool:
        return time.monotonic() < self._speaking_until

    def _send(self, message: dict[str, Any]) -> asyncio.Future | None:
        if self._ws is None or self._ws.closed:
            return None
        return asyncio.ensure_future(self._ws.send_str(json.dumps(message)))

    async def speak(self, pcm24k: bytes) -> None:
        """Sends one utterance. Speaking is assumed from now until the
        avatar reports it finished -- or the audio's length plus a
        margin, if that event never comes."""
        utterance = str(uuid.uuid4())
        self._utterance = utterance
        seconds = len(pcm24k) / (SPEECH_RATE * 2)
        self._speaking_until = time.monotonic() + seconds + 8.0
        for offset in range(0, len(pcm24k), _CHUNK_BYTES):
            chunk = pcm24k[offset : offset + _CHUNK_BYTES]
            message = {"type": "agent.speak", "audio": base64.b64encode(chunk).decode("ascii")}
            if offset == 0:
                message["event_id"] = utterance  # names the utterance
            future = self._send(message)
            if future is not None:
                await future
        future = self._send({"type": "agent.speak_end", "event_id": str(uuid.uuid4())})
        if future is not None:
            await future

    async def interrupt(self) -> None:
        future = self._send({"type": "agent.interrupt", "event_id": str(uuid.uuid4())})
        if future is not None:
            await future
        self._speaking_until = 0.0

    # -- background tasks ---------------------------------------------------

    async def _read_ws(self) -> None:
        assert self._ws is not None
        async for message in self._ws:
            if message.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                event = json.loads(message.data)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "session.state_updated":
                state = event.get("state")
                if state == "connected":
                    self._ws_connected.set()
                elif state == "disconnected":
                    self._end("websocket disconnected")
            elif kind in ("agent.speak_ended", "agent.speak_interrupted"):
                if event.get("source_event_id") == self._utterance:
                    self._speaking_until = 0.0
            elif kind == "error":
                log.warning("LiveAvatar error: %s", event.get("error"))
            elif kind == "warning":
                log.info("LiveAvatar warning: %s", event.get("warning"))
        self._end("websocket closed")

    async def _keep_alive(self) -> None:
        while self.alive:
            await asyncio.sleep(_KEEP_ALIVE_SECONDS)
            self._send({"type": "session.keep_alive", "event_id": str(uuid.uuid4())})

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
