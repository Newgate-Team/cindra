"""A montage as a package to finish in one's own DaVinci Resolve.

The timeline (FCPXML -- the very file the render agent imports) plus
every clip and the music, zipped. The free Resolve can import it by hand
(only scripting is Studio-only), so this is the montage for users with
no render machine, and a starting point for anyone who wants to adjust
the cut.

Built once per job by a Celery worker and stored with the rest of the
media rather than streamed through the API: clips run to hundreds of MB,
and R2 serves downloads without egress fees.

Where the media is: nobody knows where the user unpacks the archive, and
FCPXML 1.8 only promises absolute file URLs (relative ones are a 1.9+
media-rep feature; Resolve reportedly rejects them outside the XML's own
folder). So the timeline names a path that won't exist, and Resolve's
import asks for a folder to search for the missing clips (the manual's
documented behaviour) -- the user picks the unpacked folder, which in the
App Store (sandboxed) Resolve is also what grants it access to the files.
Everything sits flat in that one folder so there's no wrong folder to
pick; Relink Selected Clips is the fallback, in the instructions.
"""

from __future__ import annotations

import logging
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from app.content_pipeline.media_storage import upload_file
from app.db import SessionLocal
from app.models import MontageJob
from app.social_integrations.media_validation import is_own_media_url
from davinci_agent.fcpxml import Clip, Music, build_fcpxml
from davinci_agent.media_probe import UnsupportedMediaError, probe

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 4
BUILDING, READY, FAILED = "building", "ready", "failed"
# A worker killed mid-build leaves "building" behind (acks_late requeues
# the task, but not forever); after this a new request starts over.
_STALE_AFTER = timedelta(minutes=30)
_MAX_FILE_BYTES = 600 * 1024 * 1024
_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_INSTRUCTIONS = "Как открыть в DaVinci.txt"
# Never a real place: makes Resolve ask where the clips are.
_PLACEHOLDER_ROOT = PurePosixPath("/Cindra")


class PackageError(Exception):
    """The package can't be built (missing file, unreadable media)."""


class PackageTransientError(PackageError):
    """Storage hiccup -- worth another attempt."""


def _now() -> datetime:
    return datetime.now(UTC)


def _enqueue(job_id: str) -> None:
    from app.tool_integrations.tasks import build_montage_package

    build_montage_package.delay(job_id)


def request(db: Session, job: MontageJob) -> None:
    """Starts building the package unless it's built or being built."""
    # Locked: two clicks at once must not start two builds.
    db.refresh(job, with_for_update=True)
    fresh = job.package_requested_at is not None and _now() - job.package_requested_at < _STALE_AFTER
    if (job.package_status == READY and job.package_url) or (job.package_status == BUILDING and fresh):
        db.commit()  # releases the lock
        return
    job.package_status = BUILDING
    job.package_error = None
    job.package_url = None
    job.package_requested_at = _now()
    db.commit()
    try:
        _enqueue(str(job.id))
    except Exception:
        # Broker down: say so now rather than show "building" for the
        # half hour it takes to count as stuck.
        log.exception("could not enqueue montage package %s", job.id)
        job.package_status = FAILED
        job.package_error = "Не удалось начать сборку архива — попробуйте ещё раз"
        db.commit()


def _media_client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=False)


def _extension(url: str) -> str:
    return PurePosixPath(urlsplit(url).path).suffix.lower() or ".bin"


def _download(client: httpx.Client, url: str, path: Path, budget: int, label: str) -> int:
    # Spec URLs were checked at creation; checked again because this
    # worker fetches whatever the row says.
    if not is_own_media_url(url):
        raise PackageError("В монтаже есть файл не из хранилища Cindra")
    size = 0
    try:
        with client.stream("GET", url) as response, path.open("wb") as sink:
            if response.status_code >= 500:
                raise PackageTransientError(f"Хранилище Cindra ответило {response.status_code}")
            if response.status_code != 200:
                raise PackageError(
                    f"«{label}» больше нет в хранилище Cindra ({response.status_code}) — "
                    "уберите его из монтажа или загрузите заново"
                )
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > min(_MAX_FILE_BYTES, budget):
                    raise PackageError("Материалов слишком много для одного архива")
                sink.write(chunk)
    except httpx.TransportError as exc:
        raise PackageTransientError(f"Хранилище Cindra недоступно: {exc}") from exc
    return size


def _instructions(title: str, folder: str) -> str:
    return f"""«{title}» — монтажный лист Cindra для DaVinci Resolve

Работает и в бесплатной версии DaVinci Resolve.

1. Распакуйте архив. Получится папка {folder}: в ней timeline.fcpxml
   и все клипы с музыкой — ничего не переносите и не переименовывайте.
2. В DaVinci откройте или создайте проект, затем
   File → Import → Timeline… (Файл → Импорт → Таймлайн…) → timeline.fcpxml.
3. В окне импорта оставьте включённым «Automatically import source clips
   into media pool» и нажмите OK.
4. DaVinci сообщит, что не нашёл клипы, и предложит указать папку —
   согласитесь и выберите папку {folder}.
5. Если этого окна не было и клипы на таймлайне красные (Media Offline):
   в Media Pool выделите все клипы → правой кнопкой
   «Relink Selected Clips…» → та же папка {folder}.

Размер кадра и частота уже заданы в таймлайне; субтитры и рендер — на ваше
усмотрение (страница Deliver).
"""


def _build(job_id: str, spec: dict, title: str, work: Path) -> str:
    folder = f"cindra-montage-{job_id[:8]}"
    media = work / folder
    media.mkdir(parents=True)
    budget = _MAX_TOTAL_BYTES
    clips: list[Clip] = []
    with _media_client() as client:
        for index, item in enumerate(spec["clips"], start=1):
            # ASCII file names: the user's clip names live in the
            # timeline (Resolve shows those), not in paths.
            path = media / f"clip_{index:02d}{_extension(item['url'])}"
            budget -= _download(client, item["url"], path, budget, item["name"])
            still = (
                Fraction(str(item["duration_seconds"])).limit_denominator(1000)
                if item["type"] == "image"
                else None
            )
            clips.append(Clip(path, probe(path), item["name"], still_seconds=still))
        music = None
        if spec.get("music"):
            path = media / f"music{_extension(spec['music']['url'])}"
            budget -= _download(client, spec["music"]["url"], path, budget, "Музыка")
            music = Music(path, probe(path), float(spec["music"]["volume_db"]))

    timeline = build_fcpxml(
        project_name=title,
        width=spec["width"],
        height=spec["height"],
        fps=spec["fps"],
        clips=clips,
        music=music,
        media_src=lambda path: (_PLACEHOLDER_ROOT / folder / path.name).as_uri(),
    )
    archive = work / f"{folder}.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(f"{folder}/timeline.fcpxml", timeline, zipfile.ZIP_DEFLATED)
        bundle.writestr(
            f"{folder}/{_INSTRUCTIONS}", _instructions(title, folder), zipfile.ZIP_DEFLATED
        )
        for path in sorted(media.iterdir()):
            # Stored: video and audio are compressed already.
            bundle.write(path, f"{folder}/{path.name}", zipfile.ZIP_STORED)
    try:
        return upload_file(archive, "application/zip", "zip", download_name=f"{folder}.zip")
    except Exception as exc:
        raise PackageTransientError(f"Не удалось сохранить архив: {exc}") from exc


def _finish(job_id: str, *, url: str | None = None, error: str | None = None) -> None:
    with SessionLocal() as db:
        job = db.get(MontageJob, uuid.UUID(job_id))
        if job is None:
            return
        job.package_status = READY if url else FAILED
        job.package_url = url
        job.package_error = error[:500] if error else None
        db.commit()


def build(job_id: str, *, final_attempt: bool) -> None:
    with SessionLocal() as db:
        job = db.get(MontageJob, uuid.UUID(job_id))
        if job is None or job.package_status != BUILDING:
            return
        spec = dict(job.spec)
        title = job.title or f"Монтаж из {len(spec['clips'])} клипов"
    try:
        with tempfile.TemporaryDirectory(prefix="cindra-package-") as work:
            url = _build(job_id, spec, title, Path(work))
    except PackageTransientError as exc:
        if not final_attempt:
            raise
        _finish(job_id, error=str(exc))
    except (PackageError, UnsupportedMediaError, ValueError) as exc:
        _finish(job_id, error=str(exc))
    except Exception:
        log.exception("montage package %s failed", job_id)
        _finish(job_id, error="Не удалось собрать архив")
    else:
        _finish(job_id, url=url)
