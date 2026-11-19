"""Twitch chat for live streams, against devtools/twitch_simulator: the
OAuth connection through the API, fresh tokens for the agent (with the
refresh token rotating), and the agent's EventSub reader over a real
WebSocket."""

import socket
import threading
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import stream_avatars
from app.config import get_settings
from app.integrations import get_secret
from app.models import Integration, IntegrationProvider
from devtools import twitch_simulator as sim
from stream_agent.twitch_eventsub import EventSubChatReader, to_event

_AGENT = {"Authorization": "Bearer stream-agent-secret"}


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    sim.state.__init__()
    settings = get_settings()
    monkeypatch.setattr(settings, "twitch_client_id", sim.CLIENT_ID)
    monkeypatch.setattr(settings, "twitch_client_secret", sim.CLIENT_SECRET)
    monkeypatch.setattr(settings, "twitch_redirect_uri", "http://localhost:3000/oauth/twitch/callback")
    monkeypatch.setattr(settings, "twitch_base_url", "")
    monkeypatch.setattr(settings, "stream_agent_token", "stream-agent-secret")


@pytest.fixture
def routed():
    """The API's calls to id.twitch.tv / api.twitch.tv go to the simulator."""
    with TestClient(sim.app) as twitch, patch(
        "app.tool_integrations.twitch.httpx.request", twitch.request
    ):
        yield twitch


def _login(client: TestClient, email: str = "streamer@cindra.dev") -> dict:
    client.post("/auth/register", json={"email": email, "password": "supersecret1", "role": "agency"})
    token = client.post("/auth/login", json={"email": email, "password": "supersecret1"}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def _connect(client: TestClient, twitch: TestClient, headers: dict, login: str = "cindra_streamer"):
    url = urlsplit(client.post("/integrations/twitch/start", headers=headers).json()[
        "authorization_url"
    ])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert query["scope"] == "user:read:chat user:write:chat"
    assert query["force_verify"] == "true"
    assert twitch.get(f"{url.path}?{url.query}").status_code == 200
    back = twitch.post("/oauth2/approve", data={**query, "login": login, "decision": "allow"},
                       follow_redirects=False)
    code = parse_qs(urlsplit(back.headers["location"]).query)
    return client.post("/integrations/twitch/connect",
                       json={"code": code["code"][0], "state": code["state"][0]}, headers=headers)


def test_connect_stores_only_the_refresh_token_encrypted(
    client: TestClient, db: Session, routed
) -> None:
    headers = _login(client)
    response = _connect(client, routed, headers)
    assert response.status_code == 200, response.text
    assert response.json()["account_label"] == "cindra_streamer"
    row = db.scalar(select(Integration).where(Integration.provider == IntegrationProvider.twitch))
    assert row.settings["user_id"] == "100001"
    assert get_secret(row) in sim.state.refresh  # a live refresh token, stored encrypted
    assert get_secret(row) not in row.encrypted_secret


def test_start_without_config_and_a_planted_code(
    client: TestClient, routed, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim, attacker = _login(client, "victim@cindra.dev"), _login(client, "attacker@cindra.dev")
    url = urlsplit(client.post("/integrations/twitch/start", headers=attacker).json()[
        "authorization_url"
    ])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    back = routed.post("/oauth2/approve", data={**query, "decision": "allow"}, follow_redirects=False)
    code = parse_qs(urlsplit(back.headers["location"]).query)
    planted = client.post("/integrations/twitch/connect",
                          json={"code": code["code"][0], "state": code["state"][0]}, headers=victim)
    assert planted.status_code == 400
    monkeypatch.setattr(get_settings(), "twitch_client_secret", "")
    assert client.post("/integrations/twitch/start", headers=victim).status_code == 503


def _live_stream(client: TestClient, headers: dict, monkeypatch) -> dict:
    monkeypatch.setitem(
        stream_avatars.PROVIDERS, "testavatar",
        stream_avatars.AvatarProvider(IntegrationProvider.heygen, "TestAvatar"),
    )
    from app.tool_integrations import heygen

    monkeypatch.setattr(heygen, "get_current_user", lambda key: {"email": "me@x"})
    client.put("/integrations/heygen", json={"api_key": "vendor-key-123"}, headers=headers)
    stream = client.post("/streams", headers=headers, json={
        "title": "Эфир", "channel": "kaicenat", "stream_key": "live_x",
        "persona": "Ты Синди, весёлая ведущая.", "avatar_provider": "testavatar",
        "avatar": {"avatar_id": "a1"}, "post_replies_to_chat": True,
    }).json()
    client.post(f"/streams/{stream['id']}/start", headers=headers)
    job = client.post("/streams/agent/claim", json={"agent_name": "a1"}, headers=_AGENT).json()
    return job


def test_agent_gets_fresh_tokens_and_the_rotated_refresh_token_is_kept(
    client: TestClient, db: Session, routed, monkeypatch
) -> None:
    headers = _login(client)
    _connect(client, routed, headers)
    job = _live_stream(client, headers, monkeypatch)
    assert job["twitch_chat"] is True and job["post_replies_to_chat"] is True
    url = f"/streams/agent/{job['id']}/twitch-token"
    first = client.post(url, json={"agent_name": "a1"}, headers=_AGENT)
    assert first.status_code == 200, first.text
    token = first.json()
    assert (token["user_id"], token["broadcaster_login"], token["broadcaster_id"]) == (
        "100001", "kaicenat", "200001"
    )
    who = routed.get("/oauth2/validate", headers={"Authorization": f"OAuth {token['access_token']}"})
    assert who.json()["login"] == "cindra_streamer"
    # The simulator invalidates a refresh token once used: a second call
    # only works because the API saved the new one.
    second = client.post(url, json={"agent_name": "a1"}, headers=_AGENT)
    assert second.status_code == 200 and second.json()["access_token"] != token["access_token"]


def test_a_revoked_grant_is_reported_without_stopping_the_stream(
    client: TestClient, routed, monkeypatch
) -> None:
    headers = _login(client)
    _connect(client, routed, headers)
    job = _live_stream(client, headers, monkeypatch)
    routed.post("/sim/revoke", json={"login": "cindra_streamer"})
    response = client.post(f"/streams/agent/{job['id']}/twitch-token", json={"agent_name": "a1"},
                           headers=_AGENT)
    assert response.status_code == 403  # not 409: that would end the broadcast
    events = client.get(f"/streams/{job['id']}/events", headers=headers).json()
    assert any("чат читается анонимно" in e["text"] for e in events)


def test_disconnect_revokes_at_twitch(client: TestClient, routed) -> None:
    headers = _login(client)
    _connect(client, routed, headers)
    assert client.delete("/integrations/twitch", headers=headers).status_code == 204
    assert all(g.revoked for g in sim.state.refresh.values())
    assert client.get("/integrations", headers=headers).json() == []


# ---- the agent's EventSub reader, over a real WebSocket ------------------------


@pytest.fixture(scope="module")
def sim_url():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(sim.app, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def _sim_token(base: str, login: str = "cindra_streamer", scopes=("user:read:chat", "user:write:chat")):
    grant = sim._Grant(login, list(scopes))
    issued = sim._issue(grant)

    def supplier() -> dict:
        return {"access_token": issued["access_token"], "client_id": sim.CLIENT_ID,
                "user_id": sim.USERS[login][0], "login": login,
                "broadcaster_id": sim.USERS["kaicenat"][0], "broadcaster_login": "kaicenat",
                "expires_in": 14400}

    return supplier


def _reader(base: str, supplier) -> EventSubChatReader:
    reader = EventSubChatReader("kaicenat", supplier, ws_url=base.replace("http", "ws") + "/ws",
                                helix_url=base)
    reader.start()
    assert reader.connected.wait(10), reader.failed
    return reader


def _next(reader: EventSubChatReader, timeout: float = 5.0):
    return reader.events.get(timeout=timeout)


def test_reader_maps_chat_subs_raids_and_moderation(sim_url) -> None:
    reader = _reader(sim_url, _sim_token(sim_url))
    try:
        post = lambda path, body: httpx.post(f"{sim_url}{path}", json=body).json()
        assert post("/sim/chat", {"channel": "kaicenat", "user": "viewer_one", "text": "привет?",
                                  "bits": 100, "first": True, "badges": ["subscriber"]})[
            "delivered"] == 1
        message = _next(reader)
        assert (message.kind, message.user, message.text) == ("message", "viewer_one", "привет?")
        assert message.bits == 100 and message.first_message and "subscriber" in message.badges
        post("/sim/notice", {"channel": "kaicenat", "user": "second_account",
                             "notice_type": "raid", "viewer_count": 250})
        raid = _next(reader)
        assert (raid.kind, raid.notice_type, raid.viewer_count) == ("notice", "raid", 250)
        post("/sim/notice", {"channel": "kaicenat", "user": "viewer_one",
                             "notice_type": "community_sub_gift"})
        assert _next(reader).notice_type == "submysterygift"
        post("/sim/delete", {"channel": "kaicenat", "user": "viewer_one", "message_id": "m1"})
        deleted = _next(reader)
        assert (deleted.kind, deleted.message_id) == ("delete", "m1")
        post("/sim/clear_user", {"channel": "kaicenat", "user": "viewer_one"})
        assert _next(reader).kind == "clear_user"
    finally:
        reader.stop()


def test_reader_follows_session_reconnect_without_losing_or_doubling(sim_url) -> None:
    reader = _reader(sim_url, _sim_token(sim_url))
    try:
        assert httpx.post(f"{sim_url}/sim/reconnect").json()["moved"] == 1
        time.sleep(1.0)
        delivered = httpx.post(f"{sim_url}/sim/chat", json={
            "channel": "kaicenat", "user": "viewer_one", "text": "после переезда"}).json()
        assert delivered["delivered"] == 1  # only the new session holds the subscriptions
        assert _next(reader).text == "после переезда"
        assert reader.events.empty()
    finally:
        reader.stop()


def test_reader_posts_replies_and_does_not_hear_its_own_posts(sim_url) -> None:
    reader = _reader(sim_url, _sim_token(sim_url))
    try:
        reader.send("@viewer_one Привет! " + "x" * 600)
        deadline = time.time() + 5
        while not httpx.get(f"{sim_url}/sim/sent").json() and time.time() < deadline:
            time.sleep(0.05)
        sent = httpx.get(f"{sim_url}/sim/sent").json()[-1]["message"]
        assert sent.startswith("@viewer_one") and len(sent) == 500
        # Twitch echoed the post into the feed -- the avatar must not answer
        # itself; the same account typing by hand is still heard.
        time.sleep(0.5)
        httpx.post(f"{sim_url}/sim/chat", json={"channel": "kaicenat", "user": "cindra_streamer",
                                                "text": "это я, стример"})
        assert _next(reader).text == "это я, стример"
        assert reader.events.empty()
    finally:
        reader.stop()


def test_an_echo_reworded_by_twitch_is_still_recognised_by_its_id() -> None:
    reader = EventSubChatReader("kaicenat", dict)
    reader._token = {"login": "cindra_streamer"}
    reader._own_ids.append("posted-1")
    for message_id, text in (("posted-1", "текст, который Twitch поменял"), ("other", "сам")):
        reader._notify({
            "metadata": {"message_id": f"env-{message_id}"},
            "payload": {"subscription": {"type": "channel.chat.message"}, "event": {
                "broadcaster_user_login": "kaicenat", "chatter_user_login": "cindra_streamer",
                "message_id": message_id, "message": {"text": text}}},
        })
    assert [reader.events.get_nowait().text] == ["сам"] and reader.events.empty()


def test_a_revoked_grant_marks_the_reader_failed(sim_url) -> None:
    reader = _reader(sim_url, _sim_token(sim_url))
    try:
        httpx.post(f"{sim_url}/sim/revoke", json={"login": "cindra_streamer"})
        deadline = time.time() + 5
        while not reader.failed and time.time() < deadline:
            time.sleep(0.05)
        assert reader.failed and "отозвана" in reader.failed
    finally:
        reader.stop()


def test_a_hiccup_fetching_the_token_is_retried_a_refusal_is_not(sim_url) -> None:
    from stream_agent.api_client import ApiError

    good = _sim_token(sim_url)
    calls: list[int] = []

    def flaky() -> dict:
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("Cindra API 503 on /streams/agent/s1/twitch-token")
        return good()

    reader = _reader(sim_url, flaky)  # connects on the retry
    reader.stop()
    assert not reader.failed and len(calls) == 2

    def refused() -> dict:
        raise ApiError('Cindra API 403 on /streams/agent/s1/twitch-token: {"detail": "..."}')

    reader = EventSubChatReader("kaicenat", refused, ws_url=sim_url.replace("http", "ws") + "/ws",
                                helix_url=sim_url)
    reader.start()
    try:
        deadline = time.time() + 5
        while not reader.failed and time.time() < deadline:
            time.sleep(0.05)
        # The owner reads this in the stream journal: no API paths or ids.
        assert reader.failed and reader.failed.startswith("Нет доступа к чату Twitch")
        assert "/streams/" not in reader.failed
    finally:
        reader.stop()


def test_a_token_without_chat_scope_fails_fast(sim_url) -> None:
    reader = EventSubChatReader("kaicenat", _sim_token(sim_url, scopes=("user:write:chat",)),
                                ws_url=sim_url.replace("http", "ws") + "/ws", helix_url=sim_url)
    reader.start()
    try:
        deadline = time.time() + 20
        while not reader.failed and time.time() < deadline:
            time.sleep(0.1)
        assert reader.failed
        assert not reader.connected.is_set()
    finally:
        reader.stop()


def test_unknown_notices_are_ignored_and_anonymous_gifts_have_no_user() -> None:
    assert to_event("channel.chat.notification", {"notice_type": "announcement"}) is None
    gift = to_event("channel.chat.notification", {
        "notice_type": "sub_gift", "chatter_is_anonymous": True, "chatter_user_login": "x",
        "system_message": "An anonymous user gifted a sub"})
    assert (gift.user, gift.display_name, gift.notice_type) == ("", "аноним", "subgift")


# ---- the runner: falling back to anonymous chat, posting replies ----------------


class _FakeApi:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def heartbeat(self, stream_id, stats):
        return {"stop": False, "min_reply_interval_seconds": 0}

    def reply(self, stream_id, mode, **kwargs):
        return {"say": "Привет!", "emotion": "happy", "reaction": None, "audio_b64": None}

    def event(self, stream_id, kind, text):
        self.events.append((kind, text))

    def finish(self, stream_id, error=None):
        pass


class _FakeEventSub:
    def __init__(self) -> None:
        self.failed: str | None = None
        self.stopped = False
        self.sent: list[str] = []
        self.reconnects = 0
        import queue as _q

        self.events = _q.Queue()

    def start(self) -> None: ...

    def stop(self) -> None:
        self.stopped = True

    def send(self, text: str) -> None:
        self.sent.append(text)


class _Stage:
    def start(self): ...
    def speak(self, text, emotion, audio=None): ...
    def speaking(self): return False
    def healthy(self): return True
    def show(self, **_): ...
    def stop(self): ...


def _session(job_overrides: dict, chat):
    from stream_agent.brain import ChatBrain
    from stream_agent.runner import StreamSession

    job = {"id": "s1", "channel": "kaicenat", "min_reply_interval_seconds": 0, **job_overrides}
    session = StreamSession(_FakeApi(), job, lambda _job: _Stage(), chat=chat,
                            brain=ChatBrain(idle_after=3600), heartbeat_seconds=3600)
    session._stage = _Stage()
    session.brain.spoke()
    return session


def test_runner_falls_back_to_anonymous_chat_when_eventsub_gives_up(monkeypatch) -> None:
    from stream_agent import runner

    started: list[str] = []

    class _Irc(_FakeEventSub):
        def __init__(self, channel):
            super().__init__()
            started.append(channel)

    monkeypatch.setattr(runner, "ChatReader", _Irc)
    eventsub = _FakeEventSub()
    session = _session({"twitch_chat": True}, eventsub)
    eventsub.failed = "Twitch не дал доступ к чату: revoked"
    session._tick()
    assert eventsub.stopped and started == ["kaicenat"]
    assert isinstance(session.chat, _Irc)
    assert any("чат читается анонимно" in text for _, text in session.api.events)


def test_replies_are_posted_to_chat_with_a_mention_only_when_enabled() -> None:
    from stream_agent.twitch_chat import ChatEvent

    for enabled in (True, False):
        chat = _FakeEventSub()
        session = _session({"post_replies_to_chat": enabled}, chat)
        chat.events.put(ChatEvent(kind="message", channel="kaicenat", user="ann",
                                  display_name="Ann", text="как дела у тебя?", message_id="1"))
        session._tick()
        assert chat.sent == (["@ann Привет!"] if enabled else [])


def test_only_answers_and_thanks_reach_chat_and_anonymous_gifts_ping_nobody() -> None:
    from stream_agent.twitch_chat import ChatEvent

    chat = _FakeEventSub()
    session = _session({"post_replies_to_chat": True}, chat)
    session.brain.idle_after = 0
    session._tick()  # the quiet channel makes the avatar talk on its own
    assert chat.sent == []

    chat.events.put(ChatEvent(kind="notice", channel="kaicenat", user="",
                              display_name="аноним", notice_type="subgift",
                              text="Аноним подарил подписку"))
    session._tick()
    assert chat.sent == ["Привет!"]
