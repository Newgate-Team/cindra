"""One Simli Compose session, from the agent's side -- the cheaper
avatar vendor (about a tenth of LiveAvatar's per-minute price).

Protocol per docs.simli.com: POST /compose/token (x-simli-api-key,
{"faceId", "maxSessionLength", ...}) -> WebSocket
/compose/webrtc/livekit?session_token=... whose first text message is
{"livekit_url", "livekit_token"} (join within 30 s). Speech goes over
the same socket as binary PCM16 at 16 kHz; the server says "SPEAK" /
"SILENT" as the avatar starts/stops talking, "ERROR ..." / "CLOSING ..."
/ "RATE ..." when it ends the session. "SKIP" interrupts. Never send
"DONE" mid-stream: it closes the session after the current audio.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import aiohttp

from stream_agent.livekit_feed import LiveKitFeed
from stream_agent.media import resample_pcm

log = logging.getLogger("stream_agent.simli")

DEFAULT_BASE_URL = "https://api.simli.ai"
SIMLI_RATE = 16000
_CHUNK_BYTES = SIMLI_RATE  # 0.5 s of PCM16 mono
_DURATIONS = (3600, 1200, 600, 120)
# The session closes after maxIdleTime without audio; a little silence
# now and then keeps a quiet stretch of chat from ending it.
_IDLE_SECONDS = 300
_SILENCE_EVERY = 60.0
_SILENCE = b"\0" * (SIMLI_RATE * 2 // 5)  # 0.2 s


class SimliError(Exception):
    pass


class SimliAuthError(SimliError):
    pass


class SimliSession:
    def __init__(
        self,
        api_key: str,
        face_id: str,
        *,
        http: aiohttp.ClientSession,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._api_key = api_key
        self._face_id = face_id
        self._http = http
        self._base = base_url.rstrip("/")
        self.session_id: str | None = None
        self._session_token: str | None = None
        self.max_duration = 0
        self.opened_at = 0.0
        self.ended: str | None = None
        self._speaking_until = 0.0
        self._sent_at = 0.0
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._join_info: asyncio.Future | None = None
        self.feed = LiveKitFeed(self._end)
        self._tasks: list[asyncio.Task] = []

    @property
    def latest_frame(self):
        return self.feed.latest_frame

    @property
    def audio(self):
        return self.feed.audio

    # -- lifecycle --------------------------------------------------------

    async def open(self) -> None:
        info = await self.start_session()
        await self.feed.join(info["livekit_url"], info["livekit_token"])
        log.info("Simli session %s live (max %ss)", self.session_id, self.max_duration)

    async def _create_token(self) -> str:
        last: Exception | None = None
        for duration in _DURATIONS:
            body = {
                "faceId": self._face_id,
                "apiVersion": "v2",
                "handleSilence": True,
                "maxSessionLength": duration,
                "maxIdleTime": _IDLE_SECONDS,
                "audioInputFormat": "pcm16",
            }
            async with self._http.post(
                f"{self._base}/compose/token",
                json=body,
                headers={"x-simli-api-key": self._api_key},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                text = await response.text()
            if response.status in (401, 403):
                raise SimliAuthError(f"Simli отклонил ключ ({response.status})")
            if response.status >= 400:
                last = SimliError(f"Simli {response.status}: {text[:300]}")
                continue  # most likely above the plan's session cap
            token = json.loads(text).get("session_token")
            if not token:
                raise SimliError("Simli не выдал токен сессии")
            self.max_duration = duration
            return token
        raise SimliError(f"Simli не создал сессию: {last}")

    async def start_session(self) -> dict[str, Any]:
        """Token, then the session WebSocket; returns the LiveKit room the
        avatar is in."""
        self._session_token = await self._create_token()
        parts = urlsplit(self._base)
        scheme = "wss" if parts.scheme == "https" else "ws"
        url = urlunsplit(
            (scheme, parts.netloc, "/compose/webrtc/livekit",
             f"session_token={quote(self._session_token)}", "")
        )
        self._join_info = asyncio.get_running_loop().create_future()
        self._ws = await self._http.ws_connect(url, heartbeat=30)
        self._tasks.append(asyncio.create_task(self._read_ws(), name="simli-ws"))
        info = await asyncio.wait_for(self._join_info, timeout=20)
        self.session_id = info.get("session_id") or self._session_token[-12:]
        self.opened_at = time.monotonic()
        self._tasks.append(asyncio.create_task(self._keep_alive(), name="simli-keepalive"))
        return info

    async def close(self, reason: str = "USER_CLOSED") -> None:
        self._end(reason)
        for task in self._tasks:
            task.cancel()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()  # closing the socket ends the session
        await self.feed.leave()

    def _end(self, reason: str) -> None:
        if self.ended is None:
            self.ended = reason
            log.info("Simli session %s ended: %s", self.session_id, reason)

    @property
    def alive(self) -> bool:
        return self.ended is None

    @property
    def ready(self) -> bool:
        return self.alive and self.feed.has_picture

    def age(self) -> float:
        return time.monotonic() - self.opened_at if self.opened_at else 0.0

    # -- speech -----------------------------------------------------------

    @property
    def speaking(self) -> bool:
        return time.monotonic() < self._speaking_until

    async def _send(self, data: bytes | str) -> None:
        if self._ws is None or self._ws.closed:
            return
        if isinstance(data, bytes):
            await self._ws.send_bytes(data)
        else:
            await self._ws.send_str(data)
        self._sent_at = time.monotonic()

    async def speak(self, pcm24k: bytes) -> None:
        pcm = resample_pcm(pcm24k, 24000, SIMLI_RATE)
        seconds = len(pcm) / (SIMLI_RATE * 2)
        self._speaking_until = time.monotonic() + seconds + 8.0
        for offset in range(0, len(pcm), _CHUNK_BYTES):
            await self._send(pcm[offset : offset + _CHUNK_BYTES])

    async def interrupt(self) -> None:
        await self._send("SKIP")
        self._speaking_until = 0.0

    # -- background tasks ---------------------------------------------------

    async def _read_ws(self) -> None:
        assert self._ws is not None
        saw_speech = False
        async for message in self._ws:
            if message.type != aiohttp.WSMsgType.TEXT:
                continue
            text = message.data.strip()
            if self._join_info is not None and not self._join_info.done():
                try:
                    info = json.loads(text)
                except ValueError:
                    info = None
                if isinstance(info, dict) and "livekit_url" in info:
                    self._join_info.set_result(info)
                    continue
            word = text.split(maxsplit=1)[0].upper() if text else ""
            if word == "SPEAK":
                saw_speech = True
            elif word == "SILENT" and saw_speech:
                saw_speech = False
                self._speaking_until = 0.0
            elif word in ("ERROR", "CLOSING", "RATE", "STOP"):
                log.warning("Simli: %s", text)
                if self._join_info is not None and not self._join_info.done():
                    exc = SimliAuthError if "API_KEY" in text.upper() else SimliError
                    self._join_info.set_exception(exc(f"Simli: {text}"))
                self._end(text)
        if self._join_info is not None and not self._join_info.done():
            self._join_info.set_exception(SimliError("Simli закрыл соединение до старта"))
        self._end("websocket closed")

    async def _keep_alive(self) -> None:
        while self.alive:
            await asyncio.sleep(5)
            quiet = time.monotonic() - self._sent_at
            if not self.speaking and quiet >= _SILENCE_EVERY:
                await self._send(_SILENCE)
