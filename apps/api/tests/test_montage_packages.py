import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import montage_packages
from app.config import get_settings
from app.models import MontageJob, UsageEvent, UsageEventType
from davinci_agent.media_probe import probe

_MEDIA = "https://media.cindra.test"
_AGENT = {"Authorization": "Bearer agent-secret-token"}
FIXTURES = Path(__file__).parent / "fixtures" / "davinci"
_FILES = {"/avatar.mp4": "avatar.mp4", "/broll.png": "still.png", "/music.mp3": "music_cbr.mp3"}


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "r2_public_url_base", _MEDIA)
    monkeypatch.setattr(settings, "media_local_dir", str(tmp_path / "store"))
    monkeypatch.setattr(settings, "montage_agent_token", "agent-secret-token")


class _Storage:
    """Cindra's media bucket: serves the fixtures, can fail on demand."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.fail: dict[str, list[int]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        if self.fail.get(path):
            return httpx.Response(self.fail[path].pop(0))
        if path not in _FILES:
            return httpx.Response(404)
        return httpx.Response(200, content=(FIXTURES / _FILES[path]).read_bytes())


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> _Storage:
    served = _Storage()
    monkeypatch.setattr(
        montage_packages, "_media_client", lambda: httpx.Client(transport=httpx.MockTransport(served))
    )
    return served


def _login(client: TestClient, email: str) -> dict[str, str]:
    client.post("/auth/register", json={"email": email, "password": "supersecret1", "role": "agency"})
    token = client.post("/auth/login", json={"email": email, "password": "supersecret1"}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def _montage(**overrides) -> dict:
    body = {
        "clips": [
            {"type": "video", "url": f"{_MEDIA}/avatar.mp4", "name": "Аватар — приветствие"},
            {"type": "image", "url": f"{_MEDIA}/broll.png", "duration_seconds": 2.5},
        ],
        "music": {"url": f"{_MEDIA}/music.mp3"},
        "aspect_ratio": "9:16",
        "title": "Ролик недели",
    }
    body.update(overrides)
    return body


def _archive(url: str) -> zipfile.ZipFile:
    stored = Path(get_settings().media_local_dir) / url.rsplit("/", 1)[1]
    return zipfile.ZipFile(stored)


def test_timeline_only_montage_is_never_rendered_and_comes_as_a_package(
    client: TestClient, db: Session, storage: _Storage
) -> None:
    headers = _login(client, "owner@cindra.dev")
    response = client.post("/montage/jobs", json=_montage(render=False), headers=headers)

    assert response.status_code == 201, response.text
    job = response.json()
    assert (job["status"], job["render"], job["package_status"]) == ("completed", False, "ready")
    assert job["package_url"].startswith(_MEDIA) and job["package_url"].endswith(".zip")
    # Paid like any montage -- and no render machine will ever pick it up.
    used = db.scalar(
        select(func.count()).select_from(UsageEvent).where(
            UsageEvent.event_type == UsageEventType.davinci_montage
        )
    )
    assert used == 1
    claim = client.post("/montage/agent/claim", json={"agent_name": "r"}, headers=_AGENT)
    assert claim.status_code == 204


def test_package_holds_the_timeline_the_media_and_how_to_open_it(
    client: TestClient, storage: _Storage
) -> None:
    headers = _login(client, "owner@cindra.dev")
    job = client.post("/montage/jobs", json=_montage(render=False), headers=headers).json()
    folder = f"cindra-montage-{job['id'][:8]}"

    with _archive(job["package_url"]) as bundle:
        names = set(bundle.namelist())
        assert names == {
            f"{folder}/timeline.fcpxml",
            f"{folder}/Как открыть в DaVinci.txt",
            f"{folder}/clip_01.mp4",
            f"{folder}/clip_02.png",
            f"{folder}/music.mp3",
        }
        assert bundle.read(f"{folder}/clip_01.mp4") == (FIXTURES / "avatar.mp4").read_bytes()
        assert bundle.getinfo(f"{folder}/clip_01.mp4").compress_type == zipfile.ZIP_STORED
        timeline = ET.fromstring(bundle.read(f"{folder}/timeline.fcpxml"))
        instructions = bundle.read(f"{folder}/Как открыть в DaVinci.txt").decode()

    assets = {a.get("id"): a for a in timeline.iter("asset")}
    # A path that can't exist, file name intact: Resolve asks where the
    # clips are and the user points it at the unpacked folder.
    assert assets["asset_1"].get("src") == f"file:///Cindra/{folder}/clip_01.mp4"
    assert assets["asset_music"].get("src") == f"file:///Cindra/{folder}/music.mp3"
    assert assets["asset_1"].get("name") == "Аватар — приветствие"
    duration = probe(FIXTURES / "avatar.mp4").duration
    assert assets["asset_1"].get("duration") == f"{duration.numerator}/{duration.denominator}s"
    spine = list(timeline.find("library/event/project/sequence/spine"))
    assert [el.get("duration") for el in spine][1] == f"{int(Fraction(5, 2) * 25)}/25s"
    assert timeline.find("library/event/project").get("name") == "Ролик недели"
    assert folder in instructions and "Relink Selected Clips" in instructions


def test_a_rendered_montage_gets_its_package_once_and_free(
    client: TestClient, db: Session, storage: _Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _login(client, "owner@cindra.dev")
    job = client.post("/montage/jobs", json=_montage(), headers=headers).json()
    assert (job["render"], job["package_status"]) == (True, None)

    first = client.post(f"/montage/jobs/{job['id']}/package", headers=headers)
    assert first.status_code == 200 and first.json()["package_status"] == "ready"
    downloads = len(storage.requests)
    again = client.post(f"/montage/jobs/{job['id']}/package", headers=headers).json()
    assert again["package_url"] == first.json()["package_url"]
    assert len(storage.requests) == downloads  # built once
    used = db.scalar(select(func.count()).select_from(UsageEvent))
    assert used == 1  # the montage, not the package


def test_one_build_at_a_time_and_a_stuck_one_is_restarted(
    client: TestClient, db: Session, storage: _Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _login(client, "owner@cindra.dev")
    job_id = client.post("/montage/jobs", json=_montage(), headers=headers).json()["id"]
    sent: list[str] = []
    monkeypatch.setattr(montage_packages, "_enqueue", sent.append)

    for _ in range(2):
        body = client.post(f"/montage/jobs/{job_id}/package", headers=headers).json()
        assert body["package_status"] == "building"
    assert sent == [job_id]

    row = db.get(MontageJob, job_id)
    row.package_requested_at = datetime.now(UTC) - timedelta(hours=1)  # its worker died
    db.commit()
    client.post(f"/montage/jobs/{job_id}/package", headers=headers)
    assert sent == [job_id, job_id]


def test_a_missing_file_fails_the_package_and_a_retry_rebuilds_it(
    client: TestClient, storage: _Storage
) -> None:
    headers = _login(client, "owner@cindra.dev")
    storage.fail["/broll.png"] = [404]
    job = client.post("/montage/jobs", json=_montage(render=False), headers=headers).json()
    assert job["package_status"] == "failed"
    # Named the way the user knows the clip, not by its file in the archive.
    assert job["package_error"].startswith("«Клип 2» больше нет в хранилище")

    retried = client.post(f"/montage/jobs/{job['id']}/package", headers=headers).json()
    assert (retried["package_status"], retried["package_error"]) == ("ready", None)


def test_a_storage_hiccup_is_retried_until_the_last_attempt(
    client: TestClient, db: Session, storage: _Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _login(client, "owner@cindra.dev")
    monkeypatch.setattr(montage_packages, "_enqueue", lambda job_id: None)
    job_id = client.post("/montage/jobs", json=_montage(render=False), headers=headers).json()["id"]

    storage.fail["/avatar.mp4"] = [503, 502]
    # Celery's autoretry re-runs the task on these; only the last attempt
    # gives up and tells the user.
    with pytest.raises(montage_packages.PackageTransientError):
        montage_packages.build(job_id, final_attempt=False)
    montage_packages.build(job_id, final_attempt=True)
    db.expire_all()
    row = db.get(MontageJob, job_id)
    assert (row.package_status, row.package_error) == ("failed", "Хранилище Cindra ответило 502")

    client.post(f"/montage/jobs/{job_id}/package", headers=headers)
    montage_packages.build(job_id, final_attempt=False)
    db.expire_all()
    assert db.get(MontageJob, job_id).package_status == "ready"


def test_media_not_from_cindras_bucket_is_never_fetched(
    client: TestClient, db: Session, storage: _Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _login(client, "owner@cindra.dev")
    job_id = client.post("/montage/jobs", json=_montage(), headers=headers).json()["id"]
    row = db.get(MontageJob, job_id)
    row.spec = {**row.spec, "clips": [{**row.spec["clips"][0], "url": "http://169.254.169.254/x.mp4"}]}
    db.commit()

    body = client.post(f"/montage/jobs/{job_id}/package", headers=headers).json()
    assert body["package_status"] == "failed" and "не из хранилища" in body["package_error"]
    assert storage.requests == []


def test_someone_elses_montage_has_no_package_for_you(
    client: TestClient, storage: _Storage
) -> None:
    owner = _login(client, "owner@cindra.dev")
    job_id = client.post("/montage/jobs", json=_montage(), headers=owner).json()["id"]
    stranger = _login(client, "stranger@cindra.dev")
    assert client.post(f"/montage/jobs/{job_id}/package", headers=stranger).status_code == 404


def test_a_broker_outage_fails_fast_instead_of_building_forever(
    client: TestClient, storage: _Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _login(client, "owner@cindra.dev")
    job_id = client.post("/montage/jobs", json=_montage(), headers=headers).json()["id"]

    def broker_down(job_id: str) -> None:
        raise ConnectionError("redis: connection refused")

    monkeypatch.setattr(montage_packages, "_enqueue", broker_down)
    body = client.post(f"/montage/jobs/{job_id}/package", headers=headers).json()
    assert body["package_status"] == "failed" and "попробуйте ещё раз" in body["package_error"]
