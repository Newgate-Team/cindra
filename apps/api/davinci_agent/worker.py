from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlsplit

from davinci_agent.api_client import CindraApi, JobLost
from davinci_agent.fcpxml import Clip, Music, build_fcpxml
from davinci_agent.media_probe import probe

log = logging.getLogger("davinci_agent")

_MAX_MEDIA_BYTES = 500 * 1024 * 1024
_PROGRESS_EVERY_SECONDS = 3.0


class Renderer(Protocol):
    def render(
        self,
        *,
        timeline_path: Path,
        project_name: str,
        width: int,
        height: int,
        fps: int,
        subtitles: bool,
        subtitles_language: str,
        output_dir: Path,
        output_name: str,
        on_progress: Callable[[int], None],
    ) -> Path: ...


class _Reporter:
    """Progress to the API, throttled -- but never so rarely that the
    job looks dead (the API reclaims jobs silent for 3 minutes)."""

    def __init__(self, api: CindraApi, job_id: str, clock: Callable[[], float]) -> None:
        self._api, self._job_id, self._clock = api, job_id, clock
        self._last_stage: str | None = None
        self._last_at = 0.0

    def __call__(self, stage: str, progress: int) -> None:
        now = self._clock()
        if stage == self._last_stage and now - self._last_at < _PROGRESS_EVERY_SECONDS:
            return
        self._api.progress(self._job_id, stage, max(0, min(100, progress)))
        self._last_stage, self._last_at = stage, now


def _extension(url: str) -> str:
    return PurePosixPath(urlsplit(url).path).suffix.lower() or ".bin"


def process_job(
    job: dict[str, Any],
    *,
    api: CindraApi,
    renderer: Renderer,
    work_root: Path,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    job_id = str(job["id"])
    spec = job["spec"]
    job_dir = work_root / job_id
    report = _Reporter(api, job_id, clock)
    try:
        job_dir.mkdir(parents=True, exist_ok=True)
        report("Скачиваем материалы", 5)
        clips: list[Clip] = []
        for index, item in enumerate(spec["clips"], start=1):
            path = job_dir / f"clip_{index:02d}{_extension(item['url'])}"
            api.download(item["url"], path, _MAX_MEDIA_BYTES)
            still = (
                Fraction(str(item["duration_seconds"])).limit_denominator(1000)
                if item["type"] == "image"
                else None
            )
            clips.append(Clip(path, probe(path), item["name"], still_seconds=still))
        music = None
        if spec.get("music"):
            path = job_dir / f"music{_extension(spec['music']['url'])}"
            api.download(spec["music"]["url"], path, _MAX_MEDIA_BYTES)
            music = Music(path, probe(path), float(spec["music"]["volume_db"]))

        report("Собираем монтажный лист", 15)
        project_name = f"Cindra {job_id[:8]}"
        timeline = job_dir / "timeline.fcpxml"
        timeline.write_text(
            build_fcpxml(
                project_name=project_name,
                width=spec["width"],
                height=spec["height"],
                fps=spec["fps"],
                clips=clips,
                music=music,
            ),
            encoding="utf-8",
        )

        report("Монтаж в DaVinci", 20)
        rendered = renderer.render(
            timeline_path=timeline,
            project_name=project_name,
            width=spec["width"],
            height=spec["height"],
            fps=spec["fps"],
            subtitles=bool(spec.get("subtitles")),
            subtitles_language=spec.get("subtitles_language", "auto"),
            output_dir=job_dir / "render",
            output_name="montage",
            on_progress=lambda pct: report("Рендер в DaVinci", 20 + pct * 70 // 100),
        )
        report("Загружаем готовое видео", 92)
        api.upload_result(job_id, rendered)
        log.info("job %s done", job_id)
    except JobLost:
        log.warning("job %s was handed to another agent; dropping it", job_id)
    except Exception as exc:
        log.exception("job %s failed", job_id)
        try:
            api.fail(job_id, str(exc) or exc.__class__.__name__)
        except JobLost:
            pass
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def run(
    api: CindraApi,
    renderer: Renderer,
    work_root: Path,
    *,
    once: bool = False,
    idle_seconds: float = 10.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    # Claiming doubles as the heartbeat: the API marks the agent online
    # on every claim, so an idle agent still shows as available.
    while True:
        job = api.claim()
        if job is not None:
            process_job(job, api=api, renderer=renderer, work_root=work_root)
        if once:
            return
        if job is None:
            sleep(idle_seconds)
