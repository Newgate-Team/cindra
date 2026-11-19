import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import engine
from app.main import app
from app.models import (
    MontageAgent,
    MontageJob,
    MontageStatus,
    UsageEvent,
    UsageEventType,
    User,
)

_MEDIA = "https://media.cindra.test"
_AGENT_TOKEN = "agent-secret-token"
_AGENT = {"Authorization": f"Bearer {_AGENT_TOKEN}"}


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "r2_public_url_base", _MEDIA)
    monkeypatch.setattr(settings, "media_local_dir", str(tmp_path))
    monkeypatch.setattr(settings, "montage_agent_token", _AGENT_TOKEN)


def _login(client: TestClient, email: str, role: str = "agency") -> dict[str, str]:
    client.post("/auth/register", json={"email": email, "password": "supersecret1", "role": role})
    token = client.post(
        "/auth/login", json={"email": email, "password": "supersecret1"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _montage(**overrides) -> dict:
    body = {
        "clips": [
            {"type": "video", "url": f"{_MEDIA}/avatar.mp4", "name": "Аватар"},
            {"type": "image", "url": f"{_MEDIA}/broll.png"},
        ],
        "music": {"url": f"{_MEDIA}/music.mp3"},
        "subtitles": True,
        "aspect_ratio": "9:16",
        "title": "Ролик недели",
    }
    body.update(overrides)
    return body


def _jobs(db: Session) -> list[MontageJob]:
    db.expire_all()
    return list(db.scalars(select(MontageJob).order_by(MontageJob.created_at)))


def _claim(client: TestClient, name: str = "render-1"):
    return client.post("/montage/agent/claim", json={"agent_name": name}, headers=_AGENT)


def test_create_builds_the_spec_the_agent_will_render(client: TestClient, db: Session) -> None:
    headers = _login(client, "owner@cindra.dev")
    response = client.post("/montage/jobs", json=_montage(), headers=headers)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "queued"
    spec = body["spec"]
    assert (spec["width"], spec["height"], spec["fps"]) == (1080, 1920, 25)
    assert spec["clips"][0] == {
        "type": "video",
        "url": f"{_MEDIA}/avatar.mp4",
        "name": "Аватар",
        "duration_seconds": None,
    }
    assert spec["clips"][1]["duration_seconds"] == 3.0
    assert spec["clips"][1]["name"] == "Клип 2"
    assert spec["music"] == {"url": f"{_MEDIA}/music.mp3", "volume_db": -18.0}
    db.expire_all()
    assert db.scalar(
        select(func.count()).where(UsageEvent.event_type == UsageEventType.davinci_montage)
    ) == 1


def test_media_outside_cindras_bucket_is_refused(client: TestClient, db: Session) -> None:
    """The render machine downloads every URL in a spec -- anything but
    Cindra's own bucket would make it fetch arbitrary addresses."""
    headers = _login(client, "owner@cindra.dev")
    for body in (
        _montage(clips=[{"type": "video", "url": "http://169.254.169.254/latest/meta-data"}]),
        _montage(music={"url": "https://evil.example.com/x.mp3"}),
    ):
        assert client.post("/montage/jobs", json=body, headers=headers).status_code == 400
    assert _jobs(db) == []


def test_monthly_montage_quota(client: TestClient, db: Session) -> None:
    headers = _login(client, "owner@cindra.dev")
    for _ in range(3):
        assert client.post("/montage/jobs", json=_montage(), headers=headers).status_code == 201
    refused = client.post("/montage/jobs", json=_montage(), headers=headers)
    assert refused.status_code == 402
    assert "монтажей в DaVinci" in refused.json()["detail"]
    assert len(_jobs(db)) == 3


def test_jobs_are_team_visible_and_private_otherwise(client: TestClient, db: Session) -> None:
    owner = _login(client, "owner@cindra.dev")
    team_id = client.post("/team", json={"name": "Команда"}, headers=owner).json()["id"]
    member = _login(client, "member@cindra.dev", role="solo")
    db.scalar(select(User).where(User.email == "member@cindra.dev")).team_id = team_id
    db.commit()
    job_id = client.post("/montage/jobs", json=_montage(), headers=owner).json()["id"]

    assert client.get(f"/montage/jobs/{job_id}", headers=member).status_code == 200
    assert client.get("/montage/jobs", headers=member).json()["total"] == 1
    outsider = _login(client, "outsider@cindra.dev")
    assert client.get(f"/montage/jobs/{job_id}", headers=outsider).status_code == 404
    assert client.get("/montage/jobs", headers=outsider).json()["total"] == 0


def test_agent_endpoints_need_the_configured_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    hello = {"agent_name": "render-1"}
    assert client.post("/montage/agent/claim", json=hello).status_code == 401
    wrong = {"Authorization": "Bearer nope"}
    assert client.post("/montage/agent/claim", json=hello, headers=wrong).status_code == 401
    # A user's own login token is not an agent token.
    user = _login(client, "owner@cindra.dev")
    assert client.post("/montage/agent/claim", json=hello, headers=user).status_code == 401

    monkeypatch.setattr(get_settings(), "montage_agent_token", "")
    assert client.post("/montage/agent/claim", json=hello, headers=_AGENT).status_code == 503


def test_full_agent_lifecycle(client: TestClient, db: Session) -> None:
    user = _login(client, "owner@cindra.dev")
    job_id = client.post("/montage/jobs", json=_montage(), headers=user).json()["id"]

    claimed = _claim(client)
    assert claimed.status_code == 200
    assert claimed.json()["id"] == job_id
    assert claimed.json()["spec"]["clips"][0]["url"] == f"{_MEDIA}/avatar.mp4"
    assert _claim(client).status_code == 204  # nothing else queued

    progress = client.post(
        f"/montage/agent/jobs/{job_id}/progress",
        json={"agent_name": "render-1", "stage": "Рендер в DaVinci", "progress": 40},
        headers=_AGENT,
    )
    assert progress.status_code == 204
    seen = client.get(f"/montage/jobs/{job_id}", headers=user).json()
    assert (seen["status"], seen["stage"], seen["progress"]) == ("processing", "Рендер в DaVinci", 40)

    uploaded = client.post(
        f"/montage/agent/jobs/{job_id}/result?agent_name=render-1",
        files={"file": ("montage.mp4", b"rendered-mp4-bytes", "video/mp4")},
        headers=_AGENT,
    )
    assert uploaded.status_code == 204, uploaded.text
    done = client.get(f"/montage/jobs/{job_id}", headers=user).json()
    assert done["status"] == "completed"
    assert done["progress"] == 100
    assert done["result_video_url"].startswith(f"{_MEDIA}/")


def test_result_must_be_mp4(client: TestClient) -> None:
    user = _login(client, "owner@cindra.dev")
    job_id = client.post("/montage/jobs", json=_montage(), headers=user).json()["id"]
    _claim(client)
    response = client.post(
        f"/montage/agent/jobs/{job_id}/result?agent_name=render-1",
        files={"file": ("x.html", b"<html>", "text/html")},
        headers=_AGENT,
    )
    assert response.status_code == 400


def test_agent_reported_failure_reaches_the_user(client: TestClient) -> None:
    user = _login(client, "owner@cindra.dev")
    job_id = client.post("/montage/jobs", json=_montage(), headers=user).json()["id"]
    _claim(client)
    client.post(
        f"/montage/agent/jobs/{job_id}/fail",
        json={"agent_name": "render-1", "error": "DaVinci не смог импортировать таймлайн"},
        headers=_AGENT,
    )
    job = client.get(f"/montage/jobs/{job_id}", headers=user).json()
    assert job["status"] == "failed"
    assert job["error_message"] == "DaVinci не смог импортировать таймлайн"


def test_another_agent_cannot_touch_a_job_it_did_not_claim(client: TestClient) -> None:
    user = _login(client, "owner@cindra.dev")
    job_id = client.post("/montage/jobs", json=_montage(), headers=user).json()["id"]
    _claim(client, "render-1")
    for path, kwargs in (
        ("progress", {"json": {"agent_name": "render-2", "stage": "x", "progress": 1}}),
        ("fail", {"json": {"agent_name": "render-2", "error": "x"}}),
    ):
        response = client.post(f"/montage/agent/jobs/{job_id}/{path}", headers=_AGENT, **kwargs)
        assert response.status_code == 409


def test_a_job_whose_agent_went_silent_is_reclaimed(client: TestClient, db: Session) -> None:
    user = _login(client, "owner@cindra.dev")
    job_id = client.post("/montage/jobs", json=_montage(), headers=user).json()["id"]
    _claim(client, "render-1")
    (job,) = _jobs(db)
    job.heartbeat_at = datetime.now(UTC) - timedelta(minutes=4)
    db.commit()

    reclaimed = _claim(client, "render-2")
    assert reclaimed.status_code == 200
    assert reclaimed.json()["id"] == job_id
    # The original agent comes back to life: it must drop the job.
    late = client.post(
        f"/montage/agent/jobs/{job_id}/progress",
        json={"agent_name": "render-1", "stage": "x", "progress": 90},
        headers=_AGENT,
    )
    assert late.status_code == 409
    assert _jobs(db)[0].attempts == 2


def test_a_job_that_keeps_killing_agents_fails_instead_of_looping(
    client: TestClient, db: Session
) -> None:
    user = _login(client, "owner@cindra.dev")
    client.post("/montage/jobs", json=_montage(), headers=user)
    second = client.post("/montage/jobs", json=_montage(), headers=user).json()["id"]
    stuck, _ = _jobs(db)
    stuck.status = MontageStatus.processing
    stuck.attempts = 3
    stuck.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
    db.commit()

    claimed = _claim(client)
    assert claimed.json()["id"] == second
    db.expire_all()
    assert db.get(MontageJob, stuck.id).status == MontageStatus.failed


def test_agent_online_status(client: TestClient, db: Session) -> None:
    user = _login(client, "owner@cindra.dev")
    assert client.get("/montage/agents/status", headers=user).json()["online"] is False
    client.post(
        "/montage/agent/heartbeat", json={"agent_name": "render-1", "version": "1.0"}, headers=_AGENT
    )
    assert client.get("/montage/agents/status", headers=user).json()["online"] is True

    db.expire_all()
    db.get(MontageAgent, "render-1").last_seen_at = datetime.now(UTC) - timedelta(minutes=5)
    db.commit()
    assert client.get("/montage/agents/status", headers=user).json()["online"] is False


def test_two_agents_claiming_at_once_get_different_jobs(client: TestClient, db: Session) -> None:
    """Both claims are held right before their UPDATE until both have
    run their SELECT -- the worst interleaving, made deterministic.
    Without a row lock both pick the same job; with a plain FOR UPDATE
    the second blocks on the first and the hold times out; only SKIP
    LOCKED hands each agent a different job."""
    user = _login(client, "owner@cindra.dev")
    for _ in range(2):
        client.post("/montage/jobs", json=_montage(), headers=user)

    held = threading.Barrier(2, timeout=5)

    def hold_before_claim_update(conn, cursor, statement, *args) -> None:
        if statement.lstrip().startswith("UPDATE montage_jobs"):
            held.wait()

    results: dict[str, str] = {}

    def claim(name: str) -> None:
        with TestClient(app, raise_server_exceptions=False) as own_client:
            response = _claim(own_client, name)
            results[name] = response.json()["id"] if response.status_code == 200 else ""

    event.listen(engine, "before_cursor_execute", hold_before_claim_update)
    try:
        threads = [threading.Thread(target=claim, args=(n,)) for n in ("render-1", "render-2")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        event.remove(engine, "before_cursor_execute", hold_before_claim_update)

    assert all(results.values()), results
    assert results["render-1"] != results["render-2"]
    assert {job.agent_name for job in _jobs(db)} == {"render-1", "render-2"}


def test_upload_keeps_the_file_untouched(client: TestClient, tmp_path) -> None:
    headers = _login(client, "owner@cindra.dev")
    png = (Path(__file__).parent / "fixtures" / "davinci" / "still.png").read_bytes()
    response = client.post(
        "/montage/media", files={"file": ("b.png", png, "image/png")}, headers=headers
    )
    assert response.status_code == 201, response.text
    assert response.json()["type"] == "image"
    stored = Path(get_settings().media_local_dir) / response.json()["url"].rsplit("/", 1)[1]
    assert stored.read_bytes() == png  # not downscaled like /content/attachment


@pytest.mark.parametrize("mime", ["image/gif", "video/webm", "audio/ogg", "text/html"])
def test_upload_refuses_what_the_agent_cannot_handle(client: TestClient, mime: str) -> None:
    headers = _login(client, "owner@cindra.dev")
    response = client.post(
        "/montage/media", files={"file": ("x", b"data", mime)}, headers=headers
    )
    assert response.status_code == 400
