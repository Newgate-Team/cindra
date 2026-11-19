"""Google Drive: connect + auto-save + manual save, end to end against
devtools/google_drive_simulator.py -- which refuses any request Google's
own Drive v3 discovery document doesn't describe (unknown parameter,
bad field selection, unknown body key). A pass means the calls we'd
make to real Google are well-formed, without a Google Cloud client.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import drive_exports
from app.config import get_settings
from app.content_pipeline import tasks as generation_tasks
from app.models import (
    DriveExport,
    DriveExportStatus,
    GenerationContentType,
    GenerationJob,
    GenerationStatus,
    Integration,
    MontageJob,
    MontageStatus,
    User,
)
from app.tool_integrations import google_drive
from devtools import google_drive_contract as contract
from devtools import google_drive_simulator as simulator

_MEDIA = "https://media.cindra.test"
_AGENT_TOKEN = "agent-secret-token"
_REDIRECT = "http://localhost:3000/oauth/google-drive/callback"


@pytest.fixture(autouse=True)
def _wiring(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    simulator.state.__init__()
    settings = get_settings()
    monkeypatch.setattr(settings, "google_drive_client_id", simulator.CLIENT_ID)
    monkeypatch.setattr(settings, "google_drive_client_secret", simulator.CLIENT_SECRET)
    monkeypatch.setattr(settings, "google_drive_redirect_uri", _REDIRECT)
    monkeypatch.setattr(settings, "google_api_base_url", "")
    monkeypatch.setattr(settings, "r2_public_url_base", _MEDIA)
    monkeypatch.setattr(settings, "media_local_dir", str(tmp_path))
    monkeypatch.setattr(settings, "montage_agent_token", _AGENT_TOKEN)
    # Every Google host (accounts/oauth2/www.googleapis.com) -> simulator.
    monkeypatch.setattr(google_drive, "_new_client", lambda: TestClient(simulator.app))

    def serve_media(request: httpx.Request) -> httpx.Response:
        path = tmp_path / request.url.path.lstrip("/")
        if not path.is_file():
            return httpx.Response(404)
        return httpx.Response(200, content=path.read_bytes())

    monkeypatch.setattr(
        drive_exports,
        "_media_client",
        lambda: httpx.Client(transport=httpx.MockTransport(serve_media)),
    )
    return tmp_path


def _media(tmp_path: Path, name: str, data: bytes) -> str:
    (tmp_path / name).write_bytes(data)
    return f"{_MEDIA}/{name}"


def _login(client: TestClient, email: str, role: str = "agency") -> dict[str, str]:
    client.post("/auth/register", json={"email": email, "password": "supersecret1", "role": role})
    token = client.post(
        "/auth/login", json={"email": email, "password": "supersecret1"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _consent(
    client: TestClient, headers: dict[str, str], account: str = "simulator@gmail.com"
) -> dict[str, str]:
    """What the browser does: our /start -> Google's consent page ->
    "Разрешить" -> redirect back with ?code&state."""
    start = client.post("/integrations/google-drive/start", headers=headers)
    assert start.status_code == 200, start.text
    url = urlsplit(start.json()["authorization_url"])
    assert (url.scheme, url.netloc, url.path) == ("https", "accounts.google.com", "/o/oauth2/v2/auth")
    with TestClient(simulator.app) as google:
        page = google.get(f"{url.path}?{url.query}")
        assert page.status_code == 200, page.text
        form = {k: v[0] for k, v in parse_qs(url.query).items()}
        back = google.post(
            "/o/oauth2/v2/approve",
            data={**form, "account": account, "decision": "allow"},
            follow_redirects=False,
        )
    assert back.status_code == 302
    location = urlsplit(back.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == _REDIRECT
    query = parse_qs(location.query)
    return {"code": query["code"][0], "state": query["state"][0]}


def _connect(
    client: TestClient,
    headers: dict[str, str],
    account: str = "simulator@gmail.com",
    timezone: str | None = None,
) -> dict:
    response = client.post(
        "/integrations/google-drive/connect",
        json={**_consent(client, headers, account), "timezone": timezone},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _drive_files(account: str = "simulator@gmail.com") -> list[dict]:
    return [
        f
        for f in simulator.state.files.values()
        if f["owner"] == account and f["mimeType"] != simulator.FOLDER_MIME
    ]


def _folders(account: str = "simulator@gmail.com") -> list[dict]:
    return [
        f
        for f in simulator.state.files.values()
        if f["owner"] == account and f["mimeType"] == simulator.FOLDER_MIME
    ]


def _exports(db: Session) -> list[DriveExport]:
    db.expire_all()
    return list(db.scalars(select(DriveExport).order_by(DriveExport.created_at)))


def test_our_calls_are_valid_against_googles_discovery_document() -> None:
    assert contract.parameter_errors("about", "get", {"fields": "user(displayName,emailAddress)"}) == []
    assert contract.fields_errors("About", "user(displayName,emailAddress)") == []
    assert contract.fields_errors("File", google_drive.FILE_FIELDS) == []
    assert contract.fields_errors("File", "id,trashed") == []
    assert contract.parameter_errors(
        "files", "create", {"uploadType": "resumable", "fields": google_drive.FILE_FIELDS}
    ) == []
    assert contract.body_errors(
        "File", {"name": "x.mp4", "parents": ["f"], "mimeType": "video/mp4"}
    ) == []
    # ...and the checker does catch what Google would refuse.
    assert contract.fields_errors("About", "user(email)")
    assert contract.parameter_errors("files", "get", {"fileId": "x", "feilds": "id"})
    assert contract.body_errors("File", {"name": "x", "parent": "f"})


def test_connect_creates_the_cindra_folder_and_shows_the_account(
    client: TestClient, db: Session
) -> None:
    headers = _login(client, "owner@cindra.dev")
    body = _connect(client, headers)
    assert body["provider"] == "google_drive"
    assert body["account_label"] == "simulator@gmail.com"
    assert body["auto_save"] is True
    assert body["is_own"] is True
    [folder] = _folders()
    assert folder["name"] == "Cindra"
    assert body["folder_url"].endswith(f"/drive/folders/{folder['id']}")
    listed = client.get("/integrations", headers=headers).json()
    assert [i["provider"] for i in listed] == ["google_drive"]
    # The refresh token is stored encrypted, never echoed back.
    row = db.scalar(select(Integration))
    assert row.encrypted_secret not in str(listed)
    assert "1//sim-" not in row.encrypted_secret


def test_start_without_server_config_is_a_clear_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "google_drive_client_secret", "")
    headers = _login(client, "owner@cindra.dev")
    response = client.post("/integrations/google-drive/start", headers=headers)
    assert response.status_code == 503
    assert "не настроен" in response.json()["detail"]


def test_a_code_obtained_by_someone_else_cannot_be_planted(client: TestClient) -> None:
    attacker = _login(client, "attacker@cindra.dev")
    victim = _login(client, "victim@cindra.dev")
    attackers_consent = _consent(client, attacker)
    response = client.post(
        "/integrations/google-drive/connect", json=attackers_consent, headers=victim
    )
    assert response.status_code == 400
    garbage = client.post(
        "/integrations/google-drive/connect",
        json={"code": attackers_consent["code"], "state": "not-a-jwt"},
        headers=victim,
    )
    assert garbage.status_code == 400


def test_files_are_named_in_the_connecting_browsers_timezone(
    client: TestClient, db: Session, _wiring: Path
) -> None:
    headers = _login(client, "owner@cindra.dev")
    _connect(client, headers, timezone="Asia/Almaty")
    url = _media(_wiring, "a.png", b"a")
    client.post("/integrations/google-drive/exports", json={"url": url}, headers=headers)
    [export] = _exports(db)
    local = datetime.now(UTC).astimezone(drive_exports.zone("Asia/Almaty"))
    assert export.file_name.startswith(local.strftime("%Y-%m-%d "))
    stored = db.scalar(select(Integration)).settings
    assert stored["timezone"] == "Asia/Almaty"
    # A reconnect from a browser that doesn't say keeps the known zone;
    # garbage is ignored rather than stored.
    _connect(client, headers, timezone="Not/AZone")
    db.expire_all()
    assert db.scalar(select(Integration)).settings["timezone"] == "Asia/Almaty"


def test_reconnect_keeps_folder_and_choice_new_account_gets_its_own(
    client: TestClient,
) -> None:
    headers = _login(client, "owner@cindra.dev")
    first = _connect(client, headers)
    client.patch("/integrations/google-drive", json={"auto_save": False}, headers=headers)
    again = _connect(client, headers)
    assert again["folder_url"] == first["folder_url"]
    assert again["auto_save"] is False
    assert len(_folders()) == 1

    other = _connect(client, headers, account="second.account@gmail.com")
    assert other["account_label"] == "second.account@gmail.com"
    assert other["folder_url"] != first["folder_url"]
    assert other["auto_save"] is True
    assert len(_folders("second.account@gmail.com")) == 1


def test_finished_generation_lands_in_drive_automatically(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, _wiring: Path
) -> None:
    headers = _login(client, "owner@cindra.dev")
    _connect(client, headers)
    user = db.scalar(select(User))
    image = b"\x89PNG generated image bytes"
    url = _media(_wiring, "gen.png", image)
    monkeypatch.setattr(
        generation_tasks, "get_generator", lambda _type: lambda _payload: {"image_url": url}
    )
    job = GenerationJob(
        user_id=user.id,
        content_type=GenerationContentType.image,
        status=GenerationStatus.queued,
        input_payload={"topic": "Запуск продукта"},
    )
    db.add(job)
    db.commit()

    generation_tasks.run_generation_job.delay(str(job.id))

    [export] = _exports(db)
    assert export.status == DriveExportStatus.completed, export.error_message
    assert export.origin == "generation"
    [file] = _drive_files()
    assert simulator.state.contents[file["id"]] == image
    assert file["mimeType"] == "image/png"
    assert file["name"].endswith(" Изображение — Запуск продукта.png")
    assert file["parents"] == [_folders()[0]["id"]]
    assert export.web_view_link.endswith(f"/file/d/{file['id']}/view")
    db.refresh(job)
    assert job.status == GenerationStatus.completed


def test_auto_save_off_leaves_drive_alone_but_manual_save_works(
    client: TestClient, db: Session, _wiring: Path
) -> None:
    headers = _login(client, "owner@cindra.dev")
    _connect(client, headers)
    client.patch("/integrations/google-drive", json={"auto_save": False}, headers=headers)
    user = db.scalar(select(User))
    url = _media(_wiring, "clip.mp4", b"mp4-bytes")
    job = GenerationJob(
        user_id=user.id,
        content_type=GenerationContentType.video,
        status=GenerationStatus.completed,
        input_payload={"provider": "heygen"},
        output_payload={"video_url": url},
    )
    db.add(job)
    db.commit()
    drive_exports.queue_generation_export(db, job)
    assert _exports(db) == []

    saved = client.post(
        "/integrations/google-drive/exports",
        json={"url": url, "title": "Мой ролик"},
        headers=headers,
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["status"] == "completed"
    assert saved.json()["web_view_link"]
    [file] = _drive_files()
    assert file["name"].endswith(" Видео — Мой ролик.mp4")
    page = client.get("/integrations/google-drive/exports", headers=headers).json()
    assert page["total"] == 1
    assert page["items"][0]["file_name"] == file["name"]
    one = client.get(f"/integrations/google-drive/exports/{saved.json()['id']}", headers=headers)
    assert one.json()["status"] == "completed"
    stranger = _login(client, "stranger@cindra.dev")
    assert client.get(
        f"/integrations/google-drive/exports/{saved.json()['id']}", headers=stranger
    ).status_code == 404

    # Saving it again points at the copy already in Drive.
    again = client.post(
        "/integrations/google-drive/exports", json={"url": url}, headers=headers
    )
    assert again.json()["id"] == saved.json()["id"]
    assert len(_drive_files()) == 1
    by_url = client.get(
        "/integrations/google-drive/exports", params={"url": url}, headers=headers
    ).json()
    assert [item["id"] for item in by_url["items"]] == [saved.json()["id"]]
    other = client.get(
        "/integrations/google-drive/exports", params={"url": f"{_MEDIA}/nope.png"}, headers=headers
    ).json()
    assert other["total"] == 0


def test_finished_montage_lands_in_drive(client: TestClient, db: Session) -> None:
    headers = _login(client, "owner@cindra.dev")
    _connect(client, headers)
    user = db.scalar(select(User))
    job = MontageJob(
        user_id=user.id,
        status=MontageStatus.processing,
        title="Ролик недели",
        spec={"clips": []},
        agent_name="render-1",
        claimed_at=datetime.now(UTC),
        heartbeat_at=datetime.now(UTC),
    )
    db.add(job)
    db.commit()
    response = client.post(
        f"/montage/agent/jobs/{job.id}/result?agent_name=render-1",
        files={"file": ("montage.mp4", b"rendered-montage", "video/mp4")},
        headers={"Authorization": f"Bearer {_AGENT_TOKEN}"},
    )
    assert response.status_code == 204, response.text
    [export] = _exports(db)
    assert export.status == DriveExportStatus.completed, export.error_message
    [file] = _drive_files()
    assert file["name"].endswith(" Монтаж — Ролик недели.mp4")
    assert simulator.state.contents[file["id"]] == b"rendered-montage"


def test_interrupted_upload_resumes_into_one_file(
    client: TestClient, db: Session, _wiring: Path
) -> None:
    headers = _login(client, "owner@cindra.dev")
    _connect(client, headers)
    data = bytes(range(256)) * 4000  # ~1 MB: crosses the client's chunk size
    url = _media(_wiring, "big.mp4", data)
    response = client.post(
        "/integrations/google-drive/exports",
        json={"url": url, "title": "[flaky] большой"},
        headers=headers,
    )
    assert response.status_code == 201
    # Google took half, then 503: back in the queue for Celery's retry,
    # with the upload session kept.
    [export] = _exports(db)
    assert export.status == DriveExportStatus.queued
    assert export.upload_session_url
    assert _drive_files() == []

    # What the retry runs (eager Celery doesn't re-run retries itself).
    drive_exports.run_export(str(export.id), final_attempt=False)

    [export] = _exports(db)
    assert export.status == DriveExportStatus.completed, export.error_message
    assert export.attempts == 2
    # The retry asked Google where it stopped and sent only the rest --
    # one complete file, no duplicate.
    [file] = _drive_files()
    assert simulator.state.contents[file["id"]] == data
    [upload] = simulator.state.uploads.values()
    assert upload.failed_once


def test_transient_failure_on_the_last_attempt_fails_the_export(
    client: TestClient, db: Session, _wiring: Path
) -> None:
    headers = _login(client, "owner@cindra.dev")
    _connect(client, headers)
    user = db.scalar(select(User))
    export = DriveExport(
        user_id=user.id,
        integration_id=db.scalar(select(Integration)).id,
        source_url=_media(_wiring, "f.mp4", b"0123456789"),
        file_name="[flaky] f.mp4",
        origin="manual",
    )
    db.add(export)
    db.commit()
    with pytest.raises(google_drive.DriveTransientError):
        drive_exports.run_export(str(export.id), final_attempt=True)
    [export] = _exports(db)
    assert export.status == DriveExportStatus.failed
    assert "так и не принял" in export.error_message


def test_expired_upload_session_starts_a_new_one(
    client: TestClient, db: Session, _wiring: Path
) -> None:
    headers = _login(client, "owner@cindra.dev")
    _connect(client, headers)
    user = db.scalar(select(User))
    integration = db.scalar(select(Integration))
    url = _media(_wiring, "x.png", b"image")
    export = DriveExport(
        user_id=user.id,
        integration_id=integration.id,
        source_url=url,
        file_name="x.png",
        origin="manual",
        upload_session_url="https://www.googleapis.com/upload/drive/v3/files"
        "?uploadType=resumable&upload_id=sim-gone",
    )
    db.add(export)
    db.commit()
    drive_exports.run_export(str(export.id), final_attempt=False)
    [export] = _exports(db)
    assert export.status == DriveExportStatus.completed, export.error_message
    assert len(_drive_files()) == 1


def test_trashed_folder_is_recreated(client: TestClient, db: Session, _wiring: Path) -> None:
    headers = _login(client, "owner@cindra.dev")
    first = _connect(client, headers)
    [old] = _folders()
    old["trashed"] = True  # the user binned the Cindra folder
    url = _media(_wiring, "a.png", b"a")
    client.post("/integrations/google-drive/exports", json={"url": url}, headers=headers)
    [export] = _exports(db)
    assert export.status == DriveExportStatus.completed
    live = [f for f in _folders() if not f["trashed"]]
    assert len(live) == 1 and live[0]["id"] != old["id"]
    assert _drive_files()[0]["parents"] == [live[0]["id"]]
    now = client.get("/integrations", headers=headers).json()[0]
    assert now["folder_url"] != first["folder_url"]


def test_revoked_access_fails_the_export_with_a_reconnect_hint(
    client: TestClient, db: Session, _wiring: Path
) -> None:
    headers = _login(client, "owner@cindra.dev")
    _connect(client, headers)
    for grant in simulator.state.refresh_tokens.values():
        grant.revoked = True  # user removed Cindra in their Google account
    url = _media(_wiring, "a.png", b"a")
    client.post("/integrations/google-drive/exports", json={"url": url}, headers=headers)
    [export] = _exports(db)
    assert export.status == DriveExportStatus.failed
    assert "подключите Google Drive заново" in export.error_message
    assert export.attempts == 1  # not retried: retrying can't fix a revoked grant


def test_full_drive_is_reported_not_retried(
    client: TestClient, db: Session, _wiring: Path
) -> None:
    headers = _login(client, "owner@cindra.dev")
    _connect(client, headers)
    url = _media(_wiring, "a.png", b"a")
    client.post(
        "/integrations/google-drive/exports", json={"url": url, "title": "[full]"}, headers=headers
    )
    [export] = _exports(db)
    assert export.status == DriveExportStatus.failed
    assert "закончилось место" in export.error_message
    assert export.attempts == 1


def test_disconnect_revokes_at_google_and_forgets_the_token(
    client: TestClient, db: Session
) -> None:
    headers = _login(client, "owner@cindra.dev")
    _connect(client, headers)
    assert client.delete("/integrations/google-drive", headers=headers).status_code == 204
    assert all(g.revoked for g in simulator.state.refresh_tokens.values())
    assert db.scalar(select(Integration)) is None
    assert client.get("/integrations", headers=headers).json() == []


def test_only_cindra_media_can_be_saved(client: TestClient, db: Session) -> None:
    headers = _login(client, "owner@cindra.dev")
    response = client.post(
        "/integrations/google-drive/exports",
        json={"url": f"{_MEDIA}/a.png"},
        headers=headers,
    )
    assert response.status_code == 404  # not connected yet
    _connect(client, headers)
    for url in ("http://169.254.169.254/latest/meta-data", "https://evil.example/a.png"):
        response = client.post(
            "/integrations/google-drive/exports", json={"url": url}, headers=headers
        )
        assert response.status_code == 400
    user = db.scalar(select(User))
    assert drive_exports.queue_export(
        db, user, url="https://evil.example/a.png", label="a", origin="generation"
    ) is None
    assert _exports(db) == []


def test_a_save_already_in_flight_is_not_uploaded_twice(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _login(client, "owner@cindra.dev")
    _connect(client, headers)
    user = db.scalar(select(User))
    sent: list[str] = []
    monkeypatch.setattr(drive_exports, "_enqueue", sent.append)
    url = f"{_MEDIA}/clip.mp4"
    first = drive_exports.queue_export(db, user, url=url, label="a", origin="generation")
    second = drive_exports.queue_export(db, user, url=url, label="a", origin="manual")
    assert first.id == second.id
    assert sent == [str(first.id)]
    assert len(_exports(db)) == 1


def test_teammate_content_goes_to_the_owners_drive(
    client: TestClient, db: Session, _wiring: Path
) -> None:
    owner = _login(client, "owner@cindra.dev")
    team_id = client.post("/team", json={"name": "Команда"}, headers=owner).json()["id"]
    member = _login(client, "member@cindra.dev", role="solo")
    member_row = db.scalar(select(User).where(User.email == "member@cindra.dev"))
    member_row.team_id = team_id
    db.commit()
    _connect(client, owner)

    seen = client.get("/integrations", headers=member).json()
    assert seen[0]["is_own"] is False
    assert client.patch(
        "/integrations/google-drive", json={"auto_save": False}, headers=member
    ).status_code == 403
    assert client.delete("/integrations/google-drive", headers=member).status_code == 403

    url = _media(_wiring, "m.png", b"member image")
    assert client.post(
        "/integrations/google-drive/exports", json={"url": url}, headers=member
    ).status_code == 201
    assert len(_drive_files()) == 1
    # Each sees only their own saves.
    assert client.get("/integrations/google-drive/exports", headers=owner).json()["total"] == 0
    assert client.get("/integrations/google-drive/exports", headers=member).json()["total"] == 1


def test_sweep_resends_lost_and_stuck_exports(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, _wiring: Path
) -> None:
    headers = _login(client, "owner@cindra.dev")
    _connect(client, headers)
    user = db.scalar(select(User))
    integration = db.scalar(select(Integration))
    long_ago = datetime.now(UTC) - timedelta(hours=3)
    lost = DriveExport(
        user_id=user.id, integration_id=integration.id, file_name="lost.png", origin="generation",
        source_url=_media(_wiring, "lost.png", b"lost"), queued_at=long_ago,
    )
    crashed = DriveExport(
        user_id=user.id, integration_id=integration.id, file_name="crashed.png",
        origin="generation", source_url=_media(_wiring, "crashed.png", b"crashed"),
        status=DriveExportStatus.processing, attempts=1, claimed_at=long_ago,
    )
    hopeless = DriveExport(
        user_id=user.id, integration_id=integration.id, file_name="h.png", origin="generation",
        source_url=_media(_wiring, "h.png", b"h"), status=DriveExportStatus.processing,
        attempts=drive_exports.MAX_ATTEMPTS, claimed_at=long_ago,
    )
    fresh = DriveExport(
        user_id=user.id, integration_id=integration.id, file_name="fresh.png",
        origin="generation", source_url=_media(_wiring, "fresh.png", b"fresh"),
    )
    db.add_all([lost, crashed, hopeless, fresh])
    db.commit()

    assert drive_exports.sweep() == 2
    by_name = {e.file_name: e for e in _exports(db)}
    assert by_name["lost.png"].status == DriveExportStatus.completed
    assert by_name["crashed.png"].status == DriveExportStatus.completed
    assert by_name["h.png"].status == DriveExportStatus.failed
    assert by_name["fresh.png"].status == DriveExportStatus.queued  # its message may still come
    assert sorted(f["name"] for f in _drive_files()) == ["crashed.png", "lost.png"]


def test_simulator_rejects_what_google_would() -> None:
    with TestClient(simulator.app) as google:
        grant = simulator._Grant("simulator@gmail.com", simulator.SCOPE)
        token = simulator._issue_access(grant)
        auth = {"Authorization": f"Bearer {token}"}
        assert google.get("/drive/v3/about", headers=auth).status_code == 400  # fields required
        bad_fields = google.get("/drive/v3/about", params={"fields": "user(email)"}, headers=auth)
        assert bad_fields.status_code == 400
        assert bad_fields.json()["error"]["errors"][0]["location"] == "fields"
        assert google.post(
            "/drive/v3/files", params={"feilds": "id"}, json={"name": "x"}, headers=auth
        ).status_code == 400
        assert google.post(
            "/drive/v3/files", json={"name": "x", "parent": "y"}, headers=auth
        ).status_code == 400
        assert google.get("/drive/v3/about", params={"fields": "user"}).status_code == 401


def test_file_names_are_safe_and_dated() -> None:
    when = datetime(2026, 10, 22, 14, 5, tzinfo=UTC)
    name = drive_exports.file_name(f"{_MEDIA}/abc.mp4", "Монтаж", "a/b\\c\nd", when)
    assert name == "2026-10-22 14-05 Монтаж — a b c d.mp4"
    # 21:50 UTC is already the next morning in Almaty.
    late = datetime(2026, 10, 21, 21, 50, tzinfo=UTC)
    almaty = drive_exports.file_name(f"{_MEDIA}/x.mp4", "Монтаж", None, late, "Asia/Almaty")
    assert almaty == "2026-10-22 02-50 Монтаж.mp4"
    assert drive_exports.file_name(f"{_MEDIA}/x.mp4", "Монтаж", None, late, "Mars/Base") == (
        "2026-10-21 21-50 Монтаж.mp4"
    )
    assert drive_exports.file_name(f"{_MEDIA}/x.png", "Изображение", None, when) == (
        "2026-10-22 14-05 Изображение.png"
    )
    assert drive_exports.kind_label(f"{_MEDIA}/x.mov") == "Видео"
