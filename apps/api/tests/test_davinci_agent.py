"""The DaVinci agent end to end, minus DaVinci itself.

Resolve Studio isn't available here (the free edition refuses external
scripting), so FakeResolve stands in for exactly the documented object
model the driver uses -- everything else is real: the API, the job
queue, downloads, duration probing, the FCPXML, the upload.
"""

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import MontageJob, MontageStatus
from davinci_agent.api_client import CindraApi, HttpResponse
from davinci_agent.resolve_driver import ResolveError, ResolveRenderer
from davinci_agent.worker import process_job, run

FIXTURES = Path(__file__).parent / "fixtures" / "davinci"
_MEDIA = "https://media.cindra.test"
_TOKEN = "agent-secret-token"


class FakeResolve:
    def __init__(self, *, import_ok=True, render_status="Complete") -> None:
        # Resolve exposes its auto-caption keys as attributes on the
        # resolve object (instance attrs here so a test can remove one).
        self.SUBTITLE_LANGUAGE = "lang"
        self.SUBTITLE_CHARS_PER_LINE = "chars"
        self.AUTO_CAPTION_AUTO = "auto"
        self.AUTO_CAPTION_RUSSIAN = "ru"
        self.AUTO_CAPTION_ENGLISH = "en"
        self.calls: list[tuple] = []
        self.import_ok = import_ok
        self.render_status = render_status
        self.imported_xml: str | None = None
        self.settings: dict = {}
        self.render_settings: dict = {}
        self._polls = 2

    def GetProjectManager(self):
        return _Manager(self)


class _Manager:
    def __init__(self, r: FakeResolve) -> None:
        self.r = r

    def CreateProject(self, name):
        self.r.calls.append(("CreateProject", name))
        return _Project(self.r)

    def CloseProject(self, project):
        self.r.calls.append(("CloseProject",))
        return True

    def DeleteProject(self, name):
        self.r.calls.append(("DeleteProject", name))
        return True


class _Project:
    def __init__(self, r: FakeResolve) -> None:
        self.r = r

    def SetSetting(self, key, value):
        self.r.settings[key] = value
        return True

    def GetMediaPool(self):
        return _Pool(self.r)

    def SetCurrentTimeline(self, timeline):
        self.r.calls.append(("SetCurrentTimeline",))
        return True

    def SetCurrentRenderFormatAndCodec(self, fmt, codec):
        self.r.calls.append(("SetCurrentRenderFormatAndCodec", fmt, codec))
        return True

    def SetRenderSettings(self, settings):
        self.r.render_settings = settings
        return True

    def AddRenderJob(self):
        return "job-1"

    def StartRendering(self, job_ids):
        self.r.calls.append(("StartRendering", job_ids))
        target = Path(self.r.render_settings["TargetDir"])
        shutil.copy(FIXTURES / "avatar.mp4", target / f"{self.r.render_settings['CustomName']}.mp4")
        return True

    def IsRenderingInProgress(self):
        self.r._polls -= 1
        return self.r._polls >= 0

    def GetRenderJobStatus(self, job_id):
        done = self.r._polls < 0
        return {"JobStatus": self.r.render_status if done else "Rendering",
                "CompletionPercentage": 100 if done else 50}


class _Pool:
    def __init__(self, r: FakeResolve) -> None:
        self.r = r

    def ImportTimelineFromFile(self, path, options):
        self.r.calls.append(("ImportTimelineFromFile", options))
        self.r.imported_xml = Path(path).read_text(encoding="utf-8")
        return _Timeline(self.r) if self.r.import_ok else None


class _Timeline:
    def __init__(self, r: FakeResolve) -> None:
        self.r = r

    def CreateSubtitlesFromAudio(self, settings):
        self.r.calls.append(("CreateSubtitlesFromAudio", settings))
        return True


def _render(resolve, tmp_path: Path, **overrides):
    timeline = tmp_path / "timeline.fcpxml"
    timeline.write_text("<fcpxml/>")
    kwargs = {
        "timeline_path": timeline, "project_name": "Cindra abc", "width": 1080,
        "height": 1920, "fps": 25, "subtitles": True, "subtitles_language": "russian",
        "output_dir": tmp_path / "out", "output_name": "montage",
        "on_progress": lambda pct: None,
    }
    kwargs.update(overrides)
    return ResolveRenderer(resolve, sleep=lambda s: None).render(**kwargs)


def test_driver_follows_the_documented_sequence(tmp_path: Path) -> None:
    resolve = FakeResolve()
    rendered = _render(resolve, tmp_path)

    assert rendered == tmp_path / "out" / "montage.mp4"
    assert [c[0] for c in resolve.calls] == [
        "CreateProject", "ImportTimelineFromFile", "SetCurrentTimeline",
        "CreateSubtitlesFromAudio", "SetCurrentRenderFormatAndCodec", "StartRendering",
        "CloseProject", "DeleteProject",
    ]
    assert resolve.settings["timelineResolutionWidth"] == "1080"
    assert resolve.settings["timelineResolutionHeight"] == "1920"
    assert resolve.settings["timelineFrameRate"] == "25"
    assert ("SetCurrentRenderFormatAndCodec", "mp4", "H264") in resolve.calls
    captions = next(c[1] for c in resolve.calls if c[0] == "CreateSubtitlesFromAudio")
    assert captions == {"lang": "ru", "chars": 24}  # vertical frame -> short lines
    assert resolve.render_settings["ExportSubtitle"] is True
    assert resolve.render_settings["SubtitleFormat"] == "BurnIn"
    assert (resolve.render_settings["FormatWidth"], resolve.render_settings["FormatHeight"]) == (
        1080, 1920
    )


def test_without_subtitles_nothing_is_transcribed_or_burned(tmp_path: Path) -> None:
    resolve = FakeResolve()
    _render(resolve, tmp_path, subtitles=False)
    assert "CreateSubtitlesFromAudio" not in [c[0] for c in resolve.calls]
    assert "ExportSubtitle" not in resolve.render_settings


def test_failed_import_still_cleans_up_the_project(tmp_path: Path) -> None:
    resolve = FakeResolve(import_ok=False)
    with pytest.raises(ResolveError, match="импортировать"):
        _render(resolve, tmp_path)
    assert [c[0] for c in resolve.calls][-2:] == ["CloseProject", "DeleteProject"]


def test_old_resolve_without_auto_captions_says_so(tmp_path: Path) -> None:
    resolve = FakeResolve()
    del resolve.SUBTITLE_LANGUAGE
    with pytest.raises(ResolveError, match="автосубтитры"):
        _render(resolve, tmp_path)


def test_failed_render_reports_why(tmp_path: Path) -> None:
    with pytest.raises(ResolveError, match="Failed"):
        _render(FakeResolve(render_status="Failed"), tmp_path)


# --- the whole agent against the real API ---------------------------------


@pytest.fixture
def api_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "r2_public_url_base", _MEDIA)
    monkeypatch.setattr(settings, "media_local_dir", str(tmp_path / "bucket"))
    monkeypatch.setattr(settings, "montage_agent_token", _TOKEN)


def _agent(client: TestClient, name: str = "render-1") -> CindraApi:
    def send(method, url, headers, body):
        response = client.request(method, url.removeprefix("http://cindra.test"),
                                  headers=headers, content=body)
        return HttpResponse(response.status_code, response.content)

    def fetch(url):
        # Only Cindra's bucket ever reaches the agent; serve fixtures as it.
        assert url.startswith(f"{_MEDIA}/")
        yield (FIXTURES / url.rsplit("/", 1)[1]).read_bytes()

    return CindraApi("http://cindra.test", _TOKEN, name, "test", send=send, fetch=fetch)


def _user(client: TestClient) -> dict[str, str]:
    body = {"email": "creator@cindra.dev", "password": "supersecret1", "role": "solo"}
    client.post("/auth/register", json=body)
    token = client.post("/auth/login", json=body).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _order(client: TestClient, headers) -> str:
    response = client.post(
        "/montage/jobs",
        json={
            "clips": [
                {"type": "video", "url": f"{_MEDIA}/avatar.mp4", "name": "Аватар"},
                {"type": "image", "url": f"{_MEDIA}/still.png", "duration_seconds": 2},
                {"type": "video", "url": f"{_MEDIA}/insert.mov"},
            ],
            "music": {"url": f"{_MEDIA}/music_vbr.mp3", "volume_db": -20},
            "subtitles": True,
            "subtitles_language": "russian",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_agent_takes_a_job_from_order_to_finished_video(
    client: TestClient, api_settings, tmp_path: Path
) -> None:
    headers = _user(client)
    job_id = _order(client, headers)
    resolve = FakeResolve()
    work = tmp_path / "work"

    run(_agent(client), ResolveRenderer(resolve, sleep=lambda s: None), work, once=True)

    job = client.get(f"/montage/jobs/{job_id}", headers=headers).json()
    assert job["status"] == "completed", job
    assert job["result_video_url"].startswith(f"{_MEDIA}/")
    # What DaVinci was asked to import is the real montage: 3 clips in
    # order, music underneath at the requested level.
    root = ET.fromstring(resolve.imported_xml.split("\n", 2)[2])
    assert [c.get("name") for c in root.find(".//spine")] == ["Аватар", "Клип 2", "Клип 3"]
    assert root.find(".//adjust-volume").get("amount") == "-20dB"
    assert not any(work.iterdir())  # downloads and render cleaned up


def test_resolve_failure_reaches_the_user(client: TestClient, api_settings, tmp_path) -> None:
    headers = _user(client)
    job_id = _order(client, headers)
    run(_agent(client), ResolveRenderer(FakeResolve(import_ok=False), sleep=lambda s: None),
        tmp_path / "work", once=True)
    job = client.get(f"/montage/jobs/{job_id}", headers=headers).json()
    assert job["status"] == "failed"
    assert "импортировать" in job["error_message"]


def test_agent_drops_a_job_reassigned_to_another_agent(
    client: TestClient, db: Session, api_settings, tmp_path: Path
) -> None:
    headers = _user(client)
    job_id = _order(client, headers)
    agent = _agent(client, "render-1")
    job = agent.claim()

    db.expire_all()
    row = db.scalar(select(MontageJob))
    row.agent_name = "render-2"  # as if render-1 went silent and was replaced
    db.commit()

    process_job(job, api=agent, renderer=ResolveRenderer(FakeResolve(), sleep=lambda s: None),
                work_root=tmp_path / "work")
    db.expire_all()
    row = db.scalar(select(MontageJob))
    assert row.status == MontageStatus.processing  # not failed, not completed by render-1
    assert row.agent_name == "render-2"
    assert str(row.id) == job_id
