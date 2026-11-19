"""Reading a Twitch channel's chat -- anonymously, over Twitch's IRC
interface (TLS, irc.chat.twitch.tv:6697), stdlib only.

Anonymous ("justinfan") login is read-only, which is all the streamer
needs: the avatar answers out loud on stream, not in chat, so no bot
account or OAuth token is involved.

ChatReader runs the connection in a background thread and hands out
ChatEvents through a queue. Disconnects, Twitch's RECONNECT notice and
a silent socket (no PING from Twitch for too long) all lead to a
reconnect with backoff -- the stream must keep listening for 24h.
"""

from __future__ import annotations

import logging
import queue
import random
import socket
import ssl
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger("stream_agent.chat")

HOST = "irc.chat.twitch.tv"
PORT = 6697
# Twitch PINGs about every 5 minutes; silence well past that means the
# connection is dead even though the socket hasn't noticed.
_SILENCE_LIMIT = 6 * 60
_MAX_BACKOFF = 60.0
_TAG_ESCAPES = {"s": " ", ":": ";", "\\": "\\", "r": "\r", "n": "\n"}


@dataclass(frozen=True)
class ChatEvent:
    """kind: "message" | "notice" (sub, resub, gift, raid...) |
    "delete" (one message removed by a mod) | "clear_user" (a user's
    messages purged: timeout/ban) | "clear_chat" (whole chat cleared)."""

    kind: str
    channel: str
    user: str = ""
    display_name: str = ""
    text: str = ""
    message_id: str = ""
    notice_type: str = ""
    badges: frozenset[str] = field(default_factory=frozenset)
    bits: int = 0
    first_message: bool = False
    viewer_count: int = 0
    received_at: float = field(default_factory=time.time)

    @property
    def is_moderator(self) -> bool:
        return bool(self.badges & {"moderator", "broadcaster"})


def _unescape_tag(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        char = value[i]
        if char == "\\" and i + 1 < len(value):
            out.append(_TAG_ESCAPES.get(value[i + 1], value[i + 1]))
            i += 2
            continue
        if char != "\\":  # a lone trailing backslash is dropped
            out.append(char)
        i += 1
    return "".join(out)


@dataclass(frozen=True)
class IrcLine:
    tags: dict[str, str]
    prefix: str
    command: str
    params: list[str]


def parse_line(line: str) -> IrcLine | None:
    """One IRC line (IRCv3 tags included) -> its parts. None for blank."""
    line = line.rstrip("\r\n")
    if not line:
        return None
    tags: dict[str, str] = {}
    if line.startswith("@"):
        raw_tags, _, line = line[1:].partition(" ")
        for item in raw_tags.split(";"):
            key, _, value = item.partition("=")
            tags[key] = _unescape_tag(value)
    prefix = ""
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    trailing = None
    if " :" in line:
        line, _, trailing = line.partition(" :")
    elif line.startswith(":"):
        trailing, line = line[1:], ""
    parts = line.split()
    if not parts:
        return None
    params = parts[1:]
    if trailing is not None:
        params.append(trailing)
    return IrcLine(tags, prefix, parts[0].upper(), params)


def visible(text: str) -> str:
    """Drops invisible characters: chat extensions append Unicode tag
    characters (U+E0000 block) and zero-width marks to dodge Twitch's
    duplicate-message filter -- noise for the model and the overlay."""
    return "".join(
        ch for ch in text
        if not (0xE0000 <= ord(ch) <= 0xE007F or unicodedata.category(ch) == "Cf")
    ).strip()


def _badges(raw: str) -> frozenset[str]:
    return frozenset(item.split("/")[0] for item in raw.split(",") if item)


def _int(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def to_event(line: IrcLine) -> ChatEvent | None:
    """Chat-relevant IRC lines -> ChatEvent; everything else -> None."""
    if not line.params or not line.params[0].startswith("#"):
        return None
    channel = line.params[0][1:]
    tags = line.tags
    user = tags.get("login") or line.prefix.split("!")[0]
    common = {
        "channel": channel,
        "display_name": tags.get("display-name") or user,
        "badges": _badges(tags.get("badges", "")),
    }
    text = line.params[1] if len(line.params) > 1 else ""
    if line.command == "PRIVMSG":
        if text.startswith("\x01ACTION ") and text.endswith("\x01"):
            text = text[8:-1]  # "/me does something"
        text = visible(text)
        return ChatEvent(
            kind="message",
            user=user,
            text=text,
            message_id=tags.get("id", ""),
            bits=_int(tags.get("bits")),
            first_message=tags.get("first-msg") == "1",
            **common,
        )
    if line.command == "USERNOTICE":
        return ChatEvent(
            kind="notice",
            user=user,
            text=tags.get("system-msg", ""),
            notice_type=tags.get("msg-id", ""),
            message_id=tags.get("id", ""),
            viewer_count=_int(tags.get("msg-param-viewerCount")),
            **common,
        )
    if line.command == "CLEARMSG":
        return ChatEvent(
            kind="delete",
            channel=channel,
            user=tags.get("login", ""),
            message_id=tags.get("target-msg-id", ""),
        )
    if line.command == "CLEARCHAT":
        if len(line.params) > 1:
            return ChatEvent(kind="clear_user", channel=channel, user=line.params[1])
        return ChatEvent(kind="clear_chat", channel=channel)
    return None


class _Reconnect(Exception):
    pass


def _tls_connect(host: str, port: int, timeout: float) -> socket.socket:
    raw = socket.create_connection((host, port), timeout=timeout)
    return ssl.create_default_context().wrap_socket(raw, server_hostname=host)


class ChatReader:
    def __init__(
        self,
        channel: str,
        *,
        connect: Callable[[], socket.socket] | None = None,
        clock: Callable[[], float] = time.monotonic,
        silence_limit: float = _SILENCE_LIMIT,
    ) -> None:
        self.channel = channel.lower().lstrip("#")
        self.events: queue.Queue[ChatEvent] = queue.Queue(maxsize=5000)
        self.connected = threading.Event()
        self.reconnects = 0
        self._connect = connect or (lambda: _tls_connect(HOST, PORT, 15.0))
        self._clock = clock
        self._silence_limit = silence_limit
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="twitch-chat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _send(self, line: str) -> None:
        assert self._sock is not None
        self._sock.sendall(f"{line}\r\n".encode())

    def _push(self, event: ChatEvent) -> None:
        try:
            self.events.put_nowait(event)
        except queue.Full:
            # Nobody's draining fast enough: an old unanswered message is
            # worth less than a new one.
            try:
                self.events.get_nowait()
            except queue.Empty:
                pass
            self.events.put_nowait(event)

    def _session(self) -> None:
        self._sock = self._connect()
        self._sock.settimeout(1.0)
        # Silence counts from the connect, not from after the login lines:
        # read later, the clock could already include time Twitch had to
        # answer (the cause of a flaky CI test, seen on #194 and #196).
        last_heard = self._clock()
        self._send("CAP REQ :twitch.tv/tags twitch.tv/commands")
        self._send("PASS SCHMOOPIIE")  # any password: anonymous login
        self._send(f"NICK justinfan{random.randint(10000, 99999)}")
        self._send(f"JOIN #{self.channel}")
        buffer = b""
        while not self._stop.is_set():
            try:
                chunk = self._sock.recv(65536)
            except TimeoutError:
                if self._clock() - last_heard > self._silence_limit:
                    raise _Reconnect("no data from Twitch for too long") from None
                continue
            if not chunk:
                raise _Reconnect("connection closed by Twitch")
            last_heard = self._clock()
            buffer += chunk
            *lines, buffer = buffer.split(b"\r\n")
            for raw in lines:
                self._handle(raw.decode("utf-8", errors="replace"))

    def _handle(self, raw: str) -> None:
        line = parse_line(raw)
        if line is None:
            return
        if line.command == "PING":
            self._send(f"PONG :{line.params[-1] if line.params else 'tmi.twitch.tv'}")
        elif line.command == "RECONNECT":
            raise _Reconnect("Twitch asked to reconnect")
        elif line.command == "JOIN":
            if not self.connected.is_set():
                log.info("joined #%s", self.channel)
            self.connected.set()
        elif line.command == "NOTICE" and "Login" in (line.params[-1] if line.params else ""):
            raise _Reconnect(f"login refused: {line.params[-1]}")
        else:
            event = to_event(line)
            if event is not None:
                self._push(event)

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            started = self._clock()
            try:
                self._session()
            except (OSError, _Reconnect) as exc:
                if self._stop.is_set():
                    break
                log.warning("twitch chat: %s; reconnecting", exc)
            finally:
                self.connected.clear()
                if self._sock is not None:
                    try:
                        self._sock.close()
                    except OSError:
                        pass
            if self._stop.is_set():
                break
            self.reconnects += 1
            # A connection that lived a while was healthy -- start over.
            if self._clock() - started > 60:
                backoff = 1.0
            self._stop.wait(backoff + random.random())
            backoff = min(backoff * 2, _MAX_BACKOFF)
