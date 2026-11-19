"""Reading a Twitch channel's chat over EventSub WebSocket -- Twitch's
documented chat API -- as the streamer's connected account.

Same surface as twitch_chat.ChatReader (start/stop, an `events` queue
of ChatEvent, `connected`, `reconnects`), so the runner doesn't care
which one it has. On top of reading it can post a chat message (Helix
Send Chat Message), so the avatar's spoken reply can also appear in
chat.

Flow (dev.twitch.tv EventSub WebSocket docs): connect -> session_welcome
(session id + keepalive_timeout_seconds) -> within 10 s create the
subscriptions via Helix with the user token -> notifications, with a
keepalive whenever nothing else is sent. Silence past the keepalive
window means the connection is dead: reconnect and subscribe again.
session_reconnect hands over a URL whose session keeps the
subscriptions. If it can't get going at all (grant revoked, Twitch
down for minutes), `failed` is set and the runner falls back to
anonymous IRC so the stream keeps hearing its chat.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import aiohttp

from stream_agent.twitch_chat import ChatEvent, visible

log = logging.getLogger("stream_agent.eventsub")

EVENTSUB_URL = "wss://eventsub.wss.twitch.tv/ws"
HELIX_URL = "https://api.twitch.tv"
_SUBSCRIPTIONS = (
    "channel.chat.message",
    "channel.chat.notification",
    "channel.chat.message_delete",
    "channel.chat.clear_user_messages",
    "channel.chat.clear",
)
_NOTICE_TYPES = {
    "sub": "sub",
    "resub": "resub",
    "sub_gift": "subgift",
    "community_sub_gift": "submysterygift",
    "gift_paid_upgrade": "giftpaidupgrade",
    "prime_paid_upgrade": "giftpaidupgrade",
    "raid": "raid",
}
_MAX_FAILURES = 5
_MAX_MESSAGE = 500

TokenSupplier = Callable[[], dict[str, Any]]


class TokenRejected(Exception):
    pass


def to_event(subscription_type: str, event: dict[str, Any]) -> ChatEvent | None:
    """An EventSub chat notification -> the ChatEvent the brain knows."""
    channel = event.get("broadcaster_user_login", "")
    if subscription_type == "channel.chat.message":
        message = event.get("message") or {}
        return ChatEvent(
            kind="message",
            channel=channel,
            user=event.get("chatter_user_login", ""),
            display_name=event.get("chatter_user_name") or event.get("chatter_user_login", ""),
            text=visible(message.get("text", "")),
            message_id=event.get("message_id", ""),
            badges=frozenset(b.get("set_id", "") for b in event.get("badges") or []),
            bits=int((event.get("cheer") or {}).get("bits") or 0),
            # Twitch marks a first-time chatter's message as a user intro.
            first_message=event.get("message_type") == "user_intro",
        )
    if subscription_type == "channel.chat.notification":
        kind = _NOTICE_TYPES.get(event.get("notice_type", ""))
        if kind is None:
            return None
        anonymous = event.get("chatter_is_anonymous")
        user = "" if anonymous else event.get("chatter_user_login", "")
        name = "аноним" if anonymous else event.get("chatter_user_name") or user
        viewers = 0
        if kind == "raid":
            raid = event.get("raid") or {}
            user = raid.get("user_login") or user
            name = raid.get("user_name") or name
            viewers = int(raid.get("viewer_count") or 0)
        return ChatEvent(
            kind="notice",
            channel=channel,
            user=user,
            display_name=name,
            text=event.get("system_message", ""),
            notice_type=kind,
            message_id=event.get("message_id", ""),
            viewer_count=viewers,
        )
    if subscription_type == "channel.chat.message_delete":
        return ChatEvent(
            kind="delete",
            channel=channel,
            user=event.get("target_user_login", ""),
            message_id=event.get("message_id", ""),
        )
    if subscription_type == "channel.chat.clear_user_messages":
        return ChatEvent(kind="clear_user", channel=channel, user=event.get("target_user_login", ""))
    if subscription_type == "channel.chat.clear":
        return ChatEvent(kind="clear_chat", channel=channel)
    return None


class EventSubChatReader:
    def __init__(
        self,
        channel: str,
        get_token: TokenSupplier,
        *,
        ws_url: str = EVENTSUB_URL,
        helix_url: str = HELIX_URL,
    ) -> None:
        self.channel = channel.lower().lstrip("#")
        self.events: queue.Queue[ChatEvent] = queue.Queue(maxsize=5000)
        self.connected = threading.Event()
        self.reconnects = 0
        self.failed: str | None = None
        self._get_token = get_token
        self._ws_url = ws_url
        self._helix = helix_url.rstrip("/")
        self._token: dict[str, Any] | None = None
        self._token_at = 0.0
        self._seen: deque[str] = deque(maxlen=2000)
        self._seen_set: set[str] = set()
        # What we posted ourselves (see _is_own). Touched only on the loop
        # thread, like everything in _notify and _send.
        self._own_ids: deque[str] = deque(maxlen=200)
        self._own_texts: deque[str] = deque(maxlen=50)
        self._sending = 0
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="eventsub", daemon=True)
        self._task: asyncio.Future | None = None
        self._http: aiohttp.ClientSession | None = None

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self._thread.start()
        self._task = asyncio.run_coroutine_threadsafe(self._run(), self._loop)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
        if self._thread.is_alive():
            with contextlib.suppress(Exception):
                asyncio.run_coroutine_threadsafe(self._close_http(), self._loop).result(5)
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)

    async def _close_http(self) -> None:
        if self._http is not None:
            await self._http.close()

    # -- tokens -------------------------------------------------------------

    async def _token_info(self, fresh: bool = False) -> dict[str, Any]:
        expires = (self._token or {}).get("expires_in") or 0
        # At least hourly: Twitch requires apps to validate tokens hourly
        # (the API validates every token it hands out).
        stale = time.monotonic() - self._token_at > min(3600, max(60, expires - 120))
        if fresh or self._token is None or stale:
            try:
                self._token = await asyncio.get_running_loop().run_in_executor(
                    None, self._get_token
                )
            except ConnectionError:
                raise  # a hiccup on the way to the token: retried with backoff
            except Exception as exc:
                log.warning("eventsub: no Twitch token: %s", exc)
                raise TokenRejected("доступ к чату отозван или Twitch не подключён") from exc
            self._token_at = time.monotonic()
        return self._token

    def _headers(self, token: dict[str, Any]) -> dict[str, str]:
        return {"Authorization": f"Bearer {token['access_token']}", "Client-Id": token["client_id"]}

    # -- the connection -------------------------------------------------------

    async def _run(self) -> None:
        self._http = aiohttp.ClientSession()
        failures, backoff = 0, 1.0
        while True:
            try:
                await self._session(self._ws_url, subscribe=True)
                failures, backoff = 0, 1.0
            except asyncio.CancelledError:
                raise
            except TokenRejected as exc:
                # `failed` lands in the owner's stream journal: plain words,
                # the details stay in the agent's log.
                self.failed = f"Нет доступа к чату Twitch: {exc}"
                log.warning(self.failed)
                return
            except Exception as exc:
                failures += 1
                log.warning("eventsub: %s (%s/%s)", exc, failures, _MAX_FAILURES, exc_info=True)
                if failures >= _MAX_FAILURES:
                    self.failed = "Чат Twitch не отвечает несколько минут"
                    return
            finally:
                self.connected.clear()
            self.reconnects += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _welcome(self, ws: aiohttp.ClientWebSocketResponse) -> dict[str, Any]:
        message = await asyncio.wait_for(ws.receive_json(), timeout=15)
        if message.get("metadata", {}).get("message_type") != "session_welcome":
            raise ConnectionError(f"expected session_welcome, got {message.get('metadata')}")
        return message["payload"]["session"]

    async def _subscribe(self, session_id: str) -> None:
        for attempt in range(2):
            token = await self._token_info(fresh=attempt > 0)
            ok = True
            for kind in _SUBSCRIPTIONS:
                body = {
                    "type": kind,
                    "version": "1",
                    "condition": {
                        "broadcaster_user_id": token["broadcaster_id"],
                        "user_id": token["user_id"],
                    },
                    "transport": {"method": "websocket", "session_id": session_id},
                }
                async with self._http.post(
                    f"{self._helix}/helix/eventsub/subscriptions",
                    json=body,
                    headers=self._headers(token),
                ) as response:
                    if response.status == 401:
                        ok = False  # token expired under us: fresh one, once
                        break
                    if response.status not in (202, 409):  # 409: already subscribed
                        text = await response.text()
                        raise ConnectionError(f"subscribe {kind}: {response.status} {text[:200]}")
            if ok:
                return
        raise TokenRejected("Twitch отклонил токен при подписке на чат")

    async def _session(self, url: str, *, subscribe: bool) -> None:
        ws = await self._http.ws_connect(url, heartbeat=None)
        try:
            session = await self._welcome(ws)
            keepalive = float(session.get("keepalive_timeout_seconds") or 10)
            if subscribe:
                await self._subscribe(session["id"])
            self.connected.set()
            log.info("eventsub: reading #%s", self.channel)
            while True:
                try:
                    message = await asyncio.wait_for(ws.receive(), timeout=keepalive + 5)
                except TimeoutError:
                    raise ConnectionError("no keepalive from Twitch") from None
                if message.type != aiohttp.WSMsgType.TEXT:
                    raise ConnectionError(f"websocket closed ({ws.close_code})")
                data = json.loads(message.data)
                kind = data.get("metadata", {}).get("message_type")
                if kind == "notification":
                    self._notify(data)
                elif kind == "session_reconnect":
                    new_url = data["payload"]["session"]["reconnect_url"]
                    # The new session carries the subscriptions over; the
                    # old socket stays until the new one says welcome.
                    old, ws = ws, await self._http.ws_connect(new_url, heartbeat=None)
                    session = await self._welcome(ws)
                    keepalive = float(session.get("keepalive_timeout_seconds") or keepalive)
                    # The old socket kept delivering until that welcome:
                    # take what it still holds before closing it.
                    await self._drain(old)
                    await old.close()
                    log.info("eventsub: moved to a new session on Twitch's request")
                elif kind == "revocation":
                    status = data.get("payload", {}).get("subscription", {}).get("status")
                    raise TokenRejected(f"подписка отозвана ({status})")
        finally:
            await ws.close()

    async def _drain(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while True:
            try:
                message = await asyncio.wait_for(ws.receive(), timeout=0.2)
            except TimeoutError:
                return
            if message.type != aiohttp.WSMsgType.TEXT:
                return
            data = json.loads(message.data)
            if data.get("metadata", {}).get("message_type") == "notification":
                self._notify(data)

    def _notify(self, data: dict[str, Any]) -> None:
        message_id = data["metadata"].get("message_id", "")
        if message_id in self._seen_set:
            return  # Twitch may redeliver; each event counts once
        if len(self._seen) == self._seen.maxlen:
            self._seen_set.discard(self._seen[0])
        self._seen.append(message_id)
        self._seen_set.add(message_id)
        payload = data.get("payload", {})
        event = to_event(payload.get("subscription", {}).get("type", ""), payload.get("event", {}))
        if event is None or self._is_own(event):
            return
        try:
            self.events.put_nowait(event)
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                self.events.get_nowait()
            self.events.put_nowait(event)

    def _is_own(self, event: ChatEvent) -> bool:
        """Twitch delivers our own posts back as chat messages; answering
        them, the avatar would talk to itself in a loop (caught live: it
        thanked its own raid greeting). Matched by the id Twitch returned
        for the post, else by text or a post still in flight -- the echo
        can beat that HTTP answer. Not by account alone: a streamer who
        connected their own account still gets answered when typing."""
        login = (self._token or {}).get("login", "").lower()
        if event.kind != "message" or not login or event.user.lower() != login:
            return False
        return event.message_id in self._own_ids or event.text in self._own_texts or self._sending > 0

    # -- posting --------------------------------------------------------------

    def send(self, text: str) -> None:
        """Posts to chat as the connected account; fire-and-forget."""
        if self.failed or not self._thread.is_alive():
            return
        asyncio.run_coroutine_threadsafe(self._send(text[:_MAX_MESSAGE]), self._loop)

    async def _send(self, text: str) -> None:
        self._own_texts.append(visible(text))
        self._sending += 1
        try:
            token = await self._token_info()
            async with self._http.post(
                f"{self._helix}/helix/chat/messages",
                json={
                    "broadcaster_id": token["broadcaster_id"],
                    "sender_id": token["user_id"],
                    "message": text,
                },
                headers=self._headers(token),
            ) as response:
                body = await response.json(content_type=None)
            sent = (body.get("data") or [{}])[0]
            if sent.get("message_id"):
                self._own_ids.append(sent["message_id"])
            if response.status != 200 or not sent.get("is_sent"):
                log.warning("chat message not sent: %s %s", response.status, sent.get("drop_reason"))
        except Exception:
            log.exception("posting to chat failed")
        finally:
            self._sending -= 1
