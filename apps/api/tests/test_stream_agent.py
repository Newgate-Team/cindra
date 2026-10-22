"""stream_agent end to end against the real API (in-process), with the
chat and the avatar/encoder stage replaced by fakes."""

import threading
import time
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import stream_avatars
from app.config import get_settings
from app.content_pipeline.stream_replies import Reply
from app.models import IntegrationProvider, LiveStream
from app.routers import streams as streams_router
from stream_agent.api_client import HttpResponse, StreamApi
from stream_agent.brain import ChatBrain
from stream_agent.runner import StreamSession
from stream_agent.twitch_chat import ChatEvent, ChatReader

_TOKEN = "stream-agent-secret"


@pytest.fixture(autouse=True)
def _setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "stream_agent_token", _TOKEN)
    monkeypatch.setitem(
        stream_avatars.PROVIDERS,
        "testavatar",
        stream_avatars.AvatarProvider(IntegrationProvider.heygen, "TestAvatar"),
    )
    said: list[str] = []

    def fake_reply(**kwargs):
        target = kwargs.get("target")
        line = f"Отвечаю {target.author}" if target else f"Режим {kwargs['mode']}"
        said.append(line)
        return Reply(say=line, emotion="happy", reaction="👋")

    monkeypatch.setattr(streams_router, "generate_reply", fake_reply)
    monkeypatch.setattr(
        streams_router, "synthesize", lambda text, voice, emotion: b"\x01\x00" * 2400
    )
    from app.tool_integrations import heygen

    monkeypatch.setattr(heygen, "get_current_user", lambda key: {"email": "me@x"})


class _Chat(ChatReader):
    def start(self) -> None:
        self.connected.set()

    def stop(self) -> None:
        pass


class _Stage:
    instances: ClassVar[list["_Stage"]] = []

    def __init__(self, job: dict) -> None:
        self.job = job
        self.lines: list[tuple[str, str]] = []
        self.audio: list[bytes | None] = []
        self.shown: list[dict] = []
        self.alive = True
        self.started = self.stopped = False
        self._speaking_ticks = 0
        _Stage.instances.append(self)

    def start(self) -> None:
        self.started = True

    def speak(self, text: str, emotion: str, audio: bytes | None = None) -> None:
        self.lines.append((text, emotion))
        self.audio.append(audio)
        self._speaking_ticks = 2

    def speaking(self) -> bool:
        self._speaking_ticks = max(0, self._speaking_ticks - 1)
        return self._speaking_ticks > 0

    def healthy(self) -> bool:
        return self.alive

    def show(self, **overlay) -> None:
        self.shown.append(overlay)

    def stop(self) -> None:
        self.stopped = True


def _api(client: TestClient, name: str = "stream-1") -> StreamApi:
    def send(method, url, headers, body):
        response = client.request(method, url.removeprefix("http://cindra.test"),
                                  headers=headers, content=body)
        return HttpResponse(response.status_code, response.content)

    return StreamApi("http://cindra.test", _TOKEN, name, "test", send=send)


def _owner(client: TestClient) -> dict:
    client.post("/auth/register",
                json={"email": "owner@cindra.dev", "password": "supersecret1", "role": "agency"})
    token = client.post("/auth/login", json={"email": "owner@cindra.dev",
                                             "password": "supersecret1"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.put("/integrations/heygen", json={"api_key": "vendor-key-123"}, headers=headers)
    return headers


def _wait(condition, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while not condition():
        assert time.time() < deadline, "timed out"
        time.sleep(0.05)


def test_agent_answers_chat_keeps_the_log_and_stops_when_the_owner_says(
    client: TestClient, db: Session
) -> None:
    _Stage.instances.clear()
    headers = _owner(client)
    stream = client.post("/streams", headers=headers, json={
        "title": "Тест", "channel": "cindra", "stream_key": "live_x",
        "persona": "Ты Синди, весёлая ведущая.", "avatar_provider": "testavatar",
        "avatar": {"avatar_id": "a1"},
    }).json()
    client.post(f"/streams/{stream['id']}/start", headers=headers)
    row = db.get(LiveStream, stream["id"])
    row.min_reply_interval_seconds = 0  # no pacing in a test
    db.commit()

    api = _api(client)
    job = api.claim()
    assert job["stream_key"] == "live_x"
    chat = _Chat(job["channel"])
    session = StreamSession(
        api, {**job, "min_reply_interval_seconds": 0}, _Stage, chat=chat,
        brain=ChatBrain(idle_after=3600), heartbeat_seconds=0.1,
    )
    session.brain.spoke()  # skip the opening monologue
    for user, text in [("ann", "привет, ты кто?"), ("bob", "расскажи про reels")]:
        chat.events.put(ChatEvent(kind="message", channel="cindra", user=user,
                                  display_name=user, text=text, message_id=user))
    worker = threading.Thread(target=session.run)
    worker.start()
    try:
        _wait(lambda: len(_Stage.instances) == 1 and len(_Stage.instances[0].lines) == 2)
        stage = _Stage.instances[0]
        assert {line for line, _ in stage.lines} == {"Отвечаю ann", "Отвечаю bob"}
        assert stage.shown[0]["reaction"] == "👋"
        assert stage.audio == [b"\x01\x00" * 2400] * 2  # the API's voice, untouched

        client.post(f"/streams/{stream['id']}/stop", headers=headers)
        worker.join(timeout=10)
        assert not worker.is_alive()
    finally:
        session.request_stop()
        worker.join(timeout=5)

    assert stage.stopped
    done = client.get(f"/streams/{stream['id']}", headers=headers).json()
    assert done["status"] == "ended"
    assert done["stats"]["replies"] == 2
    events = client.get(f"/streams/{stream['id']}/events", headers=headers).json()
    assert [e["text"] for e in events if e["kind"] == "reply"] == ["Отвечаю bob", "Отвечаю ann"]


def test_a_dropped_avatar_is_rebuilt_without_ending_the_stream(
    client: TestClient, db: Session
) -> None:
    _Stage.instances.clear()
    headers = _owner(client)
    stream = client.post("/streams", headers=headers, json={
        "title": "Тест", "channel": "cindra", "stream_key": "live_x",
        "persona": "Ты Синди, весёлая ведущая.", "avatar_provider": "testavatar",
        "avatar": {"avatar_id": "a1"},
    }).json()
    client.post(f"/streams/{stream['id']}/start", headers=headers)
    api = _api(client)
    job = api.claim()
    session = StreamSession(api, job, _Stage, chat=_Chat("cindra"),
                            brain=ChatBrain(idle_after=3600), heartbeat_seconds=0.1)
    worker = threading.Thread(target=session.run)
    worker.start()
    try:
        _wait(lambda: len(_Stage.instances) == 1)
        _Stage.instances[0].alive = False  # the vendor session died
        _wait(lambda: len(_Stage.instances) == 2 and _Stage.instances[1].started)
        assert _Stage.instances[0].stopped
        _wait(lambda: client.get(f"/streams/{stream['id']}", headers=headers)
              .json()["stats"].get("stage_restarts") == 1)
        assert client.get(f"/streams/{stream['id']}", headers=headers).json()["status"] == "live"
    finally:
        session.request_stop()
        worker.join(timeout=10)
    assert client.get(f"/streams/{stream['id']}", headers=headers).json()["status"] == "ended"


def test_an_avatar_that_never_starts_fails_the_stream_with_the_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _owner(client)
    stream = client.post("/streams", headers=headers, json={
        "title": "Тест", "channel": "cindra", "stream_key": "live_x",
        "persona": "Ты Синди, весёлая ведущая.", "avatar_provider": "testavatar",
        "avatar": {"avatar_id": "a1"},
    }).json()
    client.post(f"/streams/{stream['id']}/start", headers=headers)

    class Broken(_Stage):
        def start(self) -> None:
            raise RuntimeError("401 invalid API key")

    from stream_agent import runner

    monkeypatch.setattr(runner, "_MAX_STAGE_FAILURES", 2)
    api = _api(client)
    session = StreamSession(api, api.claim(), Broken, chat=_Chat("cindra"))
    session._stop.wait = lambda timeout: False  # no real backoff sleep in the test
    session.run()
    failed = client.get(f"/streams/{stream['id']}", headers=headers).json()
    assert failed["status"] == "failed"
    assert "invalid API key" in failed["error_message"]
