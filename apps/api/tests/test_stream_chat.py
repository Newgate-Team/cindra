import socket
import threading
import time

import pytest

from stream_agent.twitch_chat import ChatReader, parse_line, to_event

# Real lines in the shape Twitch's IRC docs show.
_PRIVMSG = (
    "@badge-info=subscriber/8;badges=subscriber/6,premium/1;bits=100;color=#0D4200;"
    "display-name=Ронни;emotes=;first-msg=1;id=b34ccfc7-4977-403a-8a94-33c6bac34fb8;"
    "mod=0;room-id=713936733;subscriber=1;tmi-sent-ts=1642696567751;turbo=0;"
    "user-id=713936733;user-type= :ronni!ronni@ronni.tmi.twitch.tv "
    "PRIVMSG #cindra :Привет! Как дела :) и что такое Cindra?"
)
_RAID = (
    "@badges=;display-name=BigStreamer;login=bigstreamer;msg-id=raid;"
    "msg-param-viewerCount=250;system-msg=250\\sraiders\\sfrom\\sBigStreamer\\shave\\sjoined!;"
    "id=raid-1 :tmi.twitch.tv USERNOTICE #cindra"
)


def test_privmsg_with_tags_and_colon_in_text() -> None:
    event = to_event(parse_line(_PRIVMSG))
    assert event.kind == "message"
    assert event.channel == "cindra"
    assert event.user == "ronni"
    assert event.display_name == "Ронни"
    assert event.text == "Привет! Как дела :) и что такое Cindra?"
    assert event.bits == 100
    assert event.first_message is True
    assert event.badges == {"subscriber", "premium"}
    assert event.message_id == "b34ccfc7-4977-403a-8a94-33c6bac34fb8"
    assert not event.is_moderator


def test_raid_notice_unescapes_system_message() -> None:
    event = to_event(parse_line(_RAID))
    assert (event.kind, event.notice_type, event.viewer_count) == ("notice", "raid", 250)
    assert event.text == "250 raiders from BigStreamer have joined!"
    assert event.user == "bigstreamer"


def test_moderation_events() -> None:
    deleted = to_event(
        parse_line("@login=troll;target-msg-id=abc :tmi.twitch.tv CLEARMSG #cindra :bad words")
    )
    assert (deleted.kind, deleted.user, deleted.message_id) == ("delete", "troll", "abc")
    banned = to_event(parse_line("@ban-duration=600 :tmi.twitch.tv CLEARCHAT #cindra :troll"))
    assert (banned.kind, banned.user) == ("clear_user", "troll")
    assert to_event(parse_line(":tmi.twitch.tv CLEARCHAT #cindra")).kind == "clear_chat"


def test_action_and_moderator_badges() -> None:
    event = to_event(
        parse_line(
            "@badges=moderator/1;display-name=Mod :mod!mod@mod.tmi.twitch.tv "
            "PRIVMSG #cindra :\x01ACTION машет рукой\x01"
        )
    )
    assert event.text == "машет рукой"
    assert event.is_moderator


def test_tag_escapes() -> None:
    line = parse_line(r"@system-msg=a\sb\:c\\d\ne :tmi.twitch.tv USERNOTICE #x")
    assert line.tags["system-msg"] == "a b;c\\d\ne"


def test_non_chat_lines_are_ignored() -> None:
    assert parse_line("") is None
    assert to_event(parse_line(":tmi.twitch.tv 001 justinfan1 :Welcome, GLHF!")) is None
    assert parse_line("PING :tmi.twitch.tv").command == "PING"


class _FakeTwitch:
    """A local plain-TCP IRC server standing in for irc.chat.twitch.tv."""

    def __init__(self) -> None:
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.received: list[list[str]] = []
        self.connections: list[socket.socket] = []

    def connect(self) -> socket.socket:
        return socket.create_connection(("127.0.0.1", self.port), timeout=5)

    def accept(self) -> socket.socket:
        # Generous: a reconnect waits 1-2 s of backoff plus up to 1 s of
        # recv timeout, and a loaded CI box can stretch that well past 5 s.
        self.server.settimeout(20)
        conn, _ = self.server.accept()
        conn.settimeout(20)
        self.connections.append(conn)
        self.received.append([])
        return conn

    def read_until(self, conn: socket.socket, needle: str) -> list[str]:
        data = b""
        deadline = time.time() + 20
        while needle.encode() not in data and time.time() < deadline:
            data += conn.recv(4096)
        lines = data.decode().split("\r\n")
        self.received[-1].extend(lines)
        return lines


def test_reader_logs_in_anonymously_answers_ping_and_reconnects_when_told() -> None:
    twitch = _FakeTwitch()
    reader = ChatReader("Cindra", connect=twitch.connect)
    reader.start()
    try:
        conn = twitch.accept()
        login = twitch.read_until(conn, "JOIN #cindra")
        assert "CAP REQ :twitch.tv/tags twitch.tv/commands" in login
        assert any(line.startswith("NICK justinfan") for line in login)
        assert not any("oauth:" in line for line in login)
        conn.sendall(b":justinfan1!justinfan1@justinfan1.tmi.twitch.tv JOIN #cindra\r\n")
        conn.sendall((_PRIVMSG + "\r\nPING :tmi.twitch.tv\r\n").encode())
        assert reader.connected.wait(5)
        assert reader.events.get(timeout=5).text.startswith("Привет!")
        assert "PONG :tmi.twitch.tv" in twitch.read_until(conn, "PONG")

        conn.sendall(b":tmi.twitch.tv RECONNECT\r\n")
        second = twitch.accept()  # it came back on its own
        assert "JOIN #cindra" in twitch.read_until(second, "JOIN #cindra")
        second.sendall((_RAID + "\r\n").encode())
        assert reader.events.get(timeout=5).notice_type == "raid"
        assert reader.reconnects == 1
    finally:
        reader.stop()


def test_silent_connection_is_treated_as_dead() -> None:
    twitch = _FakeTwitch()
    now = [0.0]
    reader = ChatReader("cindra", connect=twitch.connect, clock=lambda: now[0], silence_limit=10)
    send = reader._send

    def send_then_get_preempted(text: str) -> None:
        send(text)
        if text.startswith("JOIN"):
            time.sleep(0.3)  # a busy CI box parks the thread right here

    reader._send = send_then_get_preempted
    reader.start()
    try:
        conn = twitch.accept()
        twitch.read_until(conn, "JOIN #cindra")
        now[0] = 11.0  # nothing from Twitch, not even a PING
        twitch.accept()
        assert reader.reconnects == 1
    finally:
        reader.stop()


def test_full_queue_drops_the_oldest_message() -> None:
    reader = ChatReader("cindra")
    reader.events.maxsize = 2
    for text in ("one", "two", "three"):
        reader._push(to_event(parse_line(f":u!u@u PRIVMSG #cindra :{text}")))
    assert [reader.events.get_nowait().text for _ in range(2)] == ["two", "three"]


@pytest.mark.parametrize("channel", ["#Cindra", "cindra", "CINDRA"])
def test_channel_name_is_normalised(channel: str) -> None:
    assert ChatReader(channel).channel == "cindra"


def test_stop_is_quick_even_while_connected() -> None:
    twitch = _FakeTwitch()
    reader = ChatReader("cindra", connect=twitch.connect)
    reader.start()
    conn = twitch.accept()
    twitch.read_until(conn, "JOIN")
    started = time.time()
    reader.stop()
    assert time.time() - started < 3
    assert not threading.Thread.is_alive(reader._thread)


def test_invisible_characters_are_stripped() -> None:
    line = parse_line(":u!u@u PRIVMSG #cindra :Jack Doherty? \U000e0000\u200b")
    assert to_event(line).text == "Jack Doherty?"
