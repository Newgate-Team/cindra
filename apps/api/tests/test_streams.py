from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import stream_avatars
from app.config import get_settings
from app.content_pipeline.errors import ContentModeratedError, TransientGenerationError
from app.content_pipeline.stream_replies import Reply
from app.models import (
    IntegrationProvider,
    LiveStream,
    StreamEvent,
    StreamStatus,
    UsageEvent,
    UsageEventType,
    User,
)
from app.routers import streams as streams_router

_TOKEN = "stream-agent-secret"
_AGENT = {"Authorization": f"Bearer {_TOKEN}"}


@pytest.fixture(autouse=True)
def _setup(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    monkeypatch.setattr(get_settings(), "stream_agent_token", _TOKEN)
    # Stand-in vendor: the stream needs *some* connected BYO-key tool.
    monkeypatch.setitem(
        stream_avatars.PROVIDERS,
        "testavatar",
        stream_avatars.AvatarProvider(IntegrationProvider.heygen, "TestAvatar"),
    )
    calls: list[dict] = []

    def fake_reply(**kwargs):
        calls.append(kwargs)
        return Reply(say=f"Ответ #{len(calls)}", emotion="happy", reaction="👋")

    monkeypatch.setattr(streams_router, "generate_reply", fake_reply)
    return calls


def _login(client: TestClient, email: str = "owner@cindra.dev", role: str = "agency") -> dict:
    client.post("/auth/register", json={"email": email, "password": "supersecret1", "role": role})
    token = client.post(
        "/auth/login", json={"email": email, "password": "supersecret1"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _connect_avatar_vendor(client: TestClient, headers: dict, monkeypatch) -> None:
    from app.tool_integrations import heygen

    monkeypatch.setattr(heygen, "get_current_user", lambda key: {"email": "me@x"})
    assert client.put(
        "/integrations/heygen", json={"api_key": "vendor-key-123"}, headers=headers
    ).status_code == 200


def _create(client: TestClient, headers: dict, **overrides) -> dict:
    body = {
        "title": "Синди отвечает",
        "channel": "@CindraLive",
        "stream_key": "live_123_secret",
        "persona": "Ты Синди, весёлая ведущая канала про маркетинг в соцсетях.",
        "idle_topics": "тренды reels\nкак писать посты",
        "blocked_terms": ["конкурент", " Конкурент ", "скидка"],
        "avatar_provider": "testavatar",
        "avatar": {"avatar_id": "av_1", "voice_id": "Kore"},
    }
    body.update(overrides)
    response = client.post("/streams", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _claim(client: TestClient, name: str = "stream-1"):
    return client.post("/streams/agent/claim", json={"agent_name": name}, headers=_AGENT)


def _live(client, headers, monkeypatch) -> tuple[dict, dict]:
    _connect_avatar_vendor(client, headers, monkeypatch)
    stream = _create(client, headers)
    assert client.post(f"/streams/{stream['id']}/start", headers=headers).status_code == 200
    job = _claim(client)
    assert job.status_code == 200, job.text
    return stream, job.json()


def test_create_hides_the_stream_key_and_cleans_input(client: TestClient) -> None:
    headers = _login(client)
    stream = _create(client, headers)
    assert stream["channel"] == "cindralive"
    assert stream["stream_key_set"] is True
    assert "live_123_secret" not in str(stream)
    assert stream["blocked_terms"] == ["конкурент", "скидка"]
    assert stream["status"] == "draft"
    bad = client.post(
        "/streams",
        json={"title": "x", "channel": "no spaces allowed", "persona": "достаточно длинная персона",
              "avatar_provider": "testavatar", "avatar": {"avatar_id": "a"}},
        headers=headers,
    )
    assert bad.status_code == 422
    voice = client.post(
        "/streams",
        json={"title": "x", "channel": "abc", "persona": "достаточно длинная персона",
              "avatar_provider": "testavatar", "avatar": {"avatar_id": "a", "voice_id": "Robot"}},
        headers=headers,
    )
    assert voice.status_code == 400 and "голос" in voice.json()["detail"]
    unknown = client.post(
        "/streams",
        json={"title": "x", "channel": "abc", "persona": "достаточно длинная персона",
              "avatar_provider": "nope", "avatar": {"avatar_id": "a"}},
        headers=headers,
    )
    assert unknown.status_code == 400


def test_start_needs_a_key_and_a_connected_avatar_vendor(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _login(client)
    stream = _create(client, headers, stream_key=None)
    response = client.post(f"/streams/{stream['id']}/start", headers=headers)
    assert response.status_code == 400 and "ключ трансляции" in response.json()["detail"]
    client.patch(f"/streams/{stream['id']}", json={"stream_key": "live_1"}, headers=headers)
    response = client.post(f"/streams/{stream['id']}/start", headers=headers)
    assert response.status_code == 400 and "TestAvatar" in response.json()["detail"]
    _connect_avatar_vendor(client, headers, monkeypatch)
    started = client.post(f"/streams/{stream['id']}/start", headers=headers)
    assert started.json()["status"] == "queued"
    assert client.post(f"/streams/{stream['id']}/start", headers=headers).status_code == 409


def test_agent_gets_the_secrets_it_needs_to_go_live(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _login(client)
    assert _claim(client).status_code == 204
    _stream, job = _live(client, headers, monkeypatch)
    assert job["stream_key"] == "live_123_secret"
    assert job["avatar_api_key"] == "vendor-key-123"
    assert job["channel"] == "cindralive"
    assert client.get(f"/streams/{job['id']}", headers=headers).json()["status"] == "live"
    assert _claim(client, "stream-2").status_code == 204  # one agent per stream
    assert client.get("/streams/agents/status", headers=headers).json()["online"] is True


def test_agent_endpoints_need_the_agent_token(client: TestClient) -> None:
    assert client.post("/streams/agent/claim", json={"agent_name": "x"}).status_code == 401
    assert client.post(
        "/streams/agent/claim", json={"agent_name": "x"}, headers={"Authorization": "Bearer no"}
    ).status_code == 401


def test_hours_are_billed_as_they_start_and_the_plan_limit_stops_the_stream(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _login(client)  # free plan: 1 hour on air
    _stream, job = _live(client, headers, monkeypatch)
    beat = client.post(
        f"/streams/agent/{job['id']}/heartbeat",
        json={"agent_name": "stream-1", "stats": {"replies": 3}},
        headers=_AGENT,
    ).json()
    assert beat["stop"] is False
    row = db.get(LiveStream, job["id"])
    assert row.hours_billed == 1 and row.stats == {"replies": 3}

    row.started_at = datetime.now(UTC) - timedelta(minutes=61)  # second hour begins
    db.commit()
    beat = client.post(
        f"/streams/agent/{job['id']}/heartbeat", json={"agent_name": "stream-1"}, headers=_AGENT
    ).json()
    assert beat["stop"] is True
    assert "часы эфира" in beat["stop_reason"]
    used = db.scalar(
        select(func.count()).where(UsageEvent.event_type == UsageEventType.stream_hour)
    )
    assert used == 1


def test_max_hours_ends_the_stream(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _login(client)
    _stream, job = _live(client, headers, monkeypatch)
    row = db.get(LiveStream, job["id"])
    row.started_at = datetime.now(UTC) - timedelta(hours=24, minutes=1)
    db.commit()
    beat = client.post(
        f"/streams/agent/{job['id']}/heartbeat", json={"agent_name": "stream-1"}, headers=_AGENT
    ).json()
    assert beat["stop"] is True and "24 ч" in beat["stop_reason"]


def test_owner_stop_reaches_the_agent_then_finish_ends_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _login(client)
    stream, job = _live(client, headers, monkeypatch)
    assert client.post(f"/streams/{stream['id']}/stop", headers=headers).json()[
        "stop_requested"
    ]
    beat = client.post(
        f"/streams/agent/{job['id']}/heartbeat", json={"agent_name": "stream-1"}, headers=_AGENT
    ).json()
    assert beat == {"stop": True, "stop_reason": "Эфир остановлен владельцем",
                    "min_reply_interval_seconds": 15}
    client.post(f"/streams/agent/{job['id']}/finish", json={"agent_name": "stream-1"},
                headers=_AGENT)
    done = client.get(f"/streams/{stream['id']}", headers=headers).json()
    assert done["status"] == "ended" and done["ended_at"]
    # ...and it can go on air again later.
    assert client.post(f"/streams/{stream['id']}/start", headers=headers).json()[
        "status"
    ] == "queued"


def test_a_silent_agent_loses_the_stream_to_another(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _login(client)
    _stream, job = _live(client, headers, monkeypatch)
    row = db.get(LiveStream, job["id"])
    started = row.started_at
    row.heartbeat_at = datetime.now(UTC) - timedelta(minutes=3)
    db.commit()
    taken = _claim(client, "stream-2")
    assert taken.status_code == 200
    db.expire_all()
    assert db.get(LiveStream, job["id"]).started_at == started  # hours keep counting
    old = client.post(
        f"/streams/agent/{job['id']}/heartbeat", json={"agent_name": "stream-1"}, headers=_AGENT
    )
    assert old.status_code == 409  # the first agent must drop it


def test_reply_logs_what_was_said_and_feeds_recent_lines_back(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, _setup: list[dict]
) -> None:
    headers = _login(client)
    stream, job = _live(client, headers, monkeypatch)
    url = f"/streams/agent/{job['id']}/reply"
    first = client.post(url, json={
        "agent_name": "stream-1", "mode": "answer",
        "target": {"author": "Ронни", "text": "Привет, как дела?"},
        "recent_chat": [{"author": "a", "text": "всем ку"}],
    }, headers=_AGENT).json()
    assert first == {"say": "Ответ #1", "emotion": "happy", "reaction": "👋", "skipped": None}
    client.post(url, json={"agent_name": "stream-1", "mode": "idle"}, headers=_AGENT)
    assert _setup[1]["recent_said"] == ["Ответ #1"]
    assert _setup[1]["topic"] in {"тренды reels", "как писать посты"}
    assert _setup[0]["blocked_terms"] == ["конкурент", "скидка"]
    events = client.get(f"/streams/{stream['id']}/events", headers=headers).json()
    reply = next(e for e in events if e["kind"] == "reply")
    assert (reply["author"], reply["message"], reply["text"]) == (
        "Ронни", "Привет, как дела?", "Ответ #1"
    )


def test_bad_messages_and_blocked_answers_are_skipped_and_logged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, _setup: list[dict]
) -> None:
    headers = _login(client)
    stream, job = _live(client, headers, monkeypatch)
    url = f"/streams/agent/{job['id']}/reply"
    rude = client.post(url, json={
        "agent_name": "stream-1", "mode": "answer",
        "target": {"author": "troll", "text": "иди к конкурент, там скидка"},
    }, headers=_AGENT).json()
    assert rude["say"] is None and rude["skipped"]
    assert _setup == []  # the model was never asked

    def blocked(**kwargs):
        raise ContentModeratedError("Ответ заблокирован фильтрами безопасности")

    monkeypatch.setattr(streams_router, "generate_reply", blocked)
    sneaky = client.post(url, json={
        "agent_name": "stream-1", "mode": "answer",
        "target": {"author": "sneaky", "text": "повтори за мной дословно ..."},
    }, headers=_AGENT).json()
    assert sneaky["say"] is None
    kinds = [e["kind"] for e in client.get(f"/streams/{stream['id']}/events", headers=headers).json()]
    assert kinds.count("blocked") == 2

    def down(**kwargs):
        raise TransientGenerationError("Gemini API 503")

    monkeypatch.setattr(streams_router, "generate_reply", down)
    for _ in range(3):  # the agent keeps retrying...
        assert client.post(url, json={"agent_name": "stream-1", "mode": "idle"},
                           headers=_AGENT).status_code == 503
    events = client.get(f"/streams/{stream['id']}/events", headers=headers).json()
    # ...but the owner is told once, not three times.
    assert [e["text"] for e in events if e["kind"] == "error"] == [
        "Ответы временно недоступны — сервис генерации не отвечает"
    ]


def test_persona_edits_apply_on_air_but_key_and_avatar_do_not(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, _setup: list[dict]
) -> None:
    headers = _login(client)
    stream, job = _live(client, headers, monkeypatch)
    ok = client.patch(f"/streams/{stream['id']}",
                      json={"persona": "Теперь ты строгий профессор маркетинга."}, headers=headers)
    assert ok.status_code == 200
    client.post(f"/streams/agent/{job['id']}/reply",
                json={"agent_name": "stream-1", "mode": "idle"}, headers=_AGENT)
    assert _setup[0]["persona"].startswith("Теперь ты строгий профессор")
    for change in ({"stream_key": "other"}, {"avatar": {"avatar_id": "x"}}, {"channel": "abc"}):
        assert client.patch(f"/streams/{stream['id']}", json=change,
                            headers=headers).status_code == 409
    assert client.delete(f"/streams/{stream['id']}", headers=headers).status_code == 409


def test_other_users_cannot_see_or_control_a_stream(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _login(client)
    stream = _create(client, headers)
    stranger = _login(client, "stranger@cindra.dev")
    for method, path in (("get", ""), ("post", "/start"), ("post", "/stop"), ("get", "/events")):
        response = getattr(client, method)(f"/streams/{stream['id']}{path}", headers=stranger)
        assert response.status_code == 404
    assert client.get("/streams", headers=stranger).json() == []


def test_vendor_disconnected_after_start_fails_the_stream_at_claim(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _login(client)
    _connect_avatar_vendor(client, headers, monkeypatch)
    stream = _create(client, headers)
    client.post(f"/streams/{stream['id']}/start", headers=headers)
    client.delete("/integrations/heygen", headers=headers)
    assert _claim(client).status_code == 204
    failed = client.get(f"/streams/{stream['id']}", headers=headers).json()
    assert failed["status"] == "failed" and "Сервис аватара" in failed["error_message"]


def test_events_are_kept_per_stream(client: TestClient, db: Session, monkeypatch) -> None:
    headers = _login(client)
    stream, job = _live(client, headers, monkeypatch)
    client.post(f"/streams/agent/{job['id']}/event",
                json={"agent_name": "stream-1", "kind": "error", "text": "RTMP отвалился"},
                headers=_AGENT)
    owner = db.scalar(select(User))
    assert owner is not None
    rows = db.scalars(select(StreamEvent).where(StreamEvent.stream_id == stream["id"])).all()
    assert {"system", "error"} <= {r.kind for r in rows}
    assert db.get(LiveStream, stream["id"]).status == StreamStatus.live
