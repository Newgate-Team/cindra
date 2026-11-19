"""Copying finished content into the user's connected Google Drive.

queue_export() records a DriveExport and hands it to Celery;
run_export() is the worker side. Every failure stays on the export row
and never on the generation/montage that produced the file -- the
content is already made and paid for, so a Drive problem can only cost
the copy, not the result.
"""

from __future__ import annotations

import logging
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, BinaryIO
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.integrations import find_integration, get_secret
from app.models import (
    DriveExport,
    DriveExportStatus,
    GenerationContentType,
    GenerationJob,
    Integration,
    IntegrationProvider,
    User,
)
from app.social_integrations.media_validation import is_own_media_url
from app.tool_integrations import google_drive

log = logging.getLogger(__name__)

FOLDER_NAME = "Cindra"
MAX_ATTEMPTS = 6
# The largest thing Cindra stores is a montage result (500 MB).
_MAX_SOURCE_BYTES = 600 * 1024 * 1024
_STUCK_QUEUED = timedelta(minutes=15)
_STUCK_PROCESSING = timedelta(hours=2)
_RECONNECT = (
    "Google Drive отклонил доступ — подключите Google Drive заново на странице «Инструменты»"
)

_MIME_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
}
_UNSAFE_NAME = re.compile(r"[\x00-\x1f/\\]+")


def _now() -> datetime:
    return datetime.now(UTC)


def _extension(url: str) -> str:
    return PurePosixPath(urlsplit(url).path).suffix.lower()


def kind_label(url: str) -> str:
    mime = _MIME_BY_EXTENSION.get(_extension(url), "")
    if mime.startswith("image/"):
        return "Изображение"
    if mime.startswith("video/"):
        return "Видео"
    if mime.startswith("audio/"):
        return "Аудио"
    return "Файл"


def zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def file_name(
    url: str,
    label: str,
    title: str | None = None,
    when: datetime | None = None,
    tz: str | None = None,
) -> str:
    """"2026-10-22 14-05 Монтаж — Запуск продукта.mp4": sorts by date in
    Drive and says what the file is without opening it. In the zone of
    whoever connected the Drive -- in UTC a late-evening render in
    Almaty or Moscow would be filed under the previous day."""
    stamp = (when or _now()).astimezone(zone(tz) or UTC).strftime("%Y-%m-%d %H-%M")
    clean_title = _UNSAFE_NAME.sub(" ", title or "").strip()[:120]
    name = f"{stamp} {label}" + (f" — {clean_title}" if clean_title else "")
    return name + _extension(url)


def _enqueue(export_id: str) -> None:
    from app.tool_integrations.tasks import export_to_drive

    try:
        export_to_drive.delay(export_id)
    except Exception:  # the sweep re-sends anything left queued
        log.exception("could not enqueue drive export %s", export_id)


def queue_export(
    db: Session,
    owner: User,
    *,
    url: str,
    label: str,
    title: str | None = None,
    origin: str,
) -> DriveExport | None:
    """Queues `url` for the owner's Drive -- their own connection or a
    teammate's, same sharing as HeyGen. None when there's no Drive or
    (for automatic saves) auto-save is off. Commits."""
    integration = find_integration(db, owner, IntegrationProvider.google_drive)
    if integration is None:
        return None
    if origin != "manual" and not integration.settings.get("auto_save", True):
        return None
    if not is_own_media_url(url):
        # Our worker downloads this URL -- same SSRF guard as TikTok's.
        log.warning("refusing drive export of a non-Cindra URL")
        return None

    export = DriveExport(
        user_id=owner.id,
        integration_id=integration.id,
        source_url=url,
        file_name=file_name(url, label, title, tz=integration.settings.get("timezone")),
        origin=origin,
    )
    try:
        with db.begin_nested():
            db.add(export)
    except IntegrityError:
        # Already on its way (a double-clicked save, or the user saving a
        # file auto-save already has): one copy in Drive, not two.
        return db.scalar(
            select(DriveExport).where(
                DriveExport.integration_id == integration.id,
                DriveExport.source_url == url,
                DriveExport.status.in_(
                    [DriveExportStatus.queued, DriveExportStatus.processing]
                ),
            )
        )
    db.commit()
    _enqueue(str(export.id))
    return export


def queue_generation_export(db: Session, job: GenerationJob) -> None:
    """Auto-save hook for a just-completed generation job. Never raises:
    the job is done and paid for whatever happens to the Drive copy."""
    try:
        output = job.output_payload or {}
        url = output.get("video_url") or output.get("image_url")
        if not url or job.content_type == GenerationContentType.text:
            return
        owner = db.get(User, job.user_id)
        if owner is None:
            return
        payload = job.input_payload or {}
        if payload.get("provider") == "heygen":
            label = "Аватар-видео"
        elif job.content_type == GenerationContentType.video:
            label = "Видео"
        else:
            label = "Изображение"
        title = payload.get("title") or payload.get("topic")
        queue_export(
            db,
            owner,
            url=url,
            label=label,
            title=title if isinstance(title, str) else None,
            origin="generation",
        )
    except Exception:
        db.rollback()
        log.exception("drive auto-save failed to queue for job %s", job.id)


def _claim(db: Session, export_id: str) -> bool:
    claimed = db.execute(
        update(DriveExport)
        .where(DriveExport.id == export_id, DriveExport.status == DriveExportStatus.queued)
        .values(
            status=DriveExportStatus.processing,
            attempts=DriveExport.attempts + 1,
            claimed_at=_now(),
        )
        .returning(DriveExport.id)
    ).first()
    db.commit()
    return claimed is not None


def _media_client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=False)


def _download(url: str, sink: BinaryIO) -> tuple[int, str | None]:
    size = 0
    try:
        with _media_client() as client, client.stream("GET", url) as response:
            if response.status_code != 200:
                if response.status_code >= 500:
                    raise google_drive.DriveTransientError(
                        f"Хранилище Cindra ответило {response.status_code}"
                    )
                raise google_drive.DriveError(
                    f"Файл больше недоступен в хранилище Cindra ({response.status_code})"
                )
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > _MAX_SOURCE_BYTES:
                    raise google_drive.DriveError("Файл слишком большой для сохранения в Drive")
                sink.write(chunk)
            content_type = response.headers.get("content-type")
    except httpx.TransportError as exc:
        raise google_drive.DriveTransientError(f"Хранилище Cindra недоступно: {exc}") from exc
    return size, content_type.split(";")[0].strip() if content_type else None


def _ensure_folder(db: Session, integration: Integration, access_token: str) -> str:
    """The "Cindra" folder, recreated if the user deleted or trashed it --
    saving must keep working without them having to reconnect."""
    folder_id = integration.settings.get("folder_id")
    if folder_id and google_drive.folder_usable(access_token, folder_id):
        return folder_id
    folder = google_drive.create_folder(access_token, FOLDER_NAME)
    integration.settings = {
        **integration.settings,
        "folder_id": folder["id"],
        "folder_url": folder.get("webViewLink"),
    }
    db.commit()
    return folder["id"]


def _upload(
    db: Session, export: DriveExport, integration: Integration, path: BinaryIO, size: int, mime: str
) -> dict[str, Any]:
    result: dict[str, Any] | int | None = None
    if export.upload_session_url:
        try:
            result = google_drive.upload_status(export.upload_session_url, size)
        except google_drive.DriveSessionExpiredError:
            export.upload_session_url = None
    if export.upload_session_url is None:
        access_token = google_drive.refresh_access_token(get_secret(integration))
        folder_id = _ensure_folder(db, integration, access_token)
        export.upload_session_url = google_drive.start_upload(
            access_token, name=export.file_name, mime_type=mime, size=size, parent_id=folder_id
        )
        # Saved before a single byte goes up: if this worker dies
        # mid-upload, the retry resumes this session -- one file in
        # Drive, not one per attempt.
        db.commit()
        result = 0
    while isinstance(result, int):
        offset = result
        result = google_drive.upload_from(export.upload_session_url, path, size, offset)
        if isinstance(result, int) and result <= offset:
            raise google_drive.DriveTransientError("Google Drive не принял часть файла")
    return result


def run_export(export_id: str, *, final_attempt: bool) -> None:
    with SessionLocal() as db:
        if not _claim(db, export_id):
            return  # someone else has it, or it's already finished
        export = db.get(DriveExport, export_id)
        integration = db.get(Integration, export.integration_id) if export else None
        if export is None or integration is None:
            return
        try:
            with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as buffer:
                size, content_type = _download(export.source_url, buffer)
                mime = (
                    _MIME_BY_EXTENSION.get(_extension(export.source_url))
                    or content_type
                    or "application/octet-stream"
                )
                drive_file = _upload(db, export, integration, buffer, size, mime)
        except google_drive.DriveTransientError as exc:
            if final_attempt or export.attempts >= MAX_ATTEMPTS:
                _fail(db, export, f"Google Drive так и не принял файл: {exc}")
            else:
                export.status = DriveExportStatus.queued
                export.queued_at = _now()
                db.commit()
            raise
        except google_drive.DriveAuthError:
            _fail(db, export, _RECONNECT)
            return
        except google_drive.DriveError as exc:
            _fail(db, export, str(exc) or "Не удалось сохранить файл в Google Drive")
            return
        except Exception:
            log.exception("drive export %s crashed", export_id)
            db.rollback()
            _fail(db, export, "Не удалось сохранить файл в Google Drive")
            return

        export.status = DriveExportStatus.completed
        export.drive_file_id = drive_file.get("id")
        export.web_view_link = drive_file.get("webViewLink")
        export.upload_session_url = None
        export.error_message = None
        export.completed_at = _now()
        db.commit()


def _fail(db: Session, export: DriveExport, message: str) -> None:
    export.status = DriveExportStatus.failed
    export.error_message = message[:2000]
    export.upload_session_url = None
    export.completed_at = _now()
    db.commit()


def sweep() -> int:
    """Re-sends exports whose Celery message never ran: the enqueue
    failed, the broker lost it, or the worker died mid-upload (with
    acks_late the redelivery finds the row "processing" and leaves it,
    so it lands here after _STUCK_PROCESSING)."""
    now = _now()
    resend: list[str] = []
    with SessionLocal() as db:
        stuck = db.scalars(
            select(DriveExport)
            .where(
                (
                    (DriveExport.status == DriveExportStatus.queued)
                    & (DriveExport.queued_at < now - _STUCK_QUEUED)
                )
                | (
                    (DriveExport.status == DriveExportStatus.processing)
                    & (DriveExport.claimed_at < now - _STUCK_PROCESSING)
                )
            )
            .with_for_update(skip_locked=True)
        ).all()
        for export in stuck:
            if export.attempts >= MAX_ATTEMPTS:
                export.status = DriveExportStatus.failed
                export.error_message = "Google Drive так и не принял файл за несколько попыток"
                export.upload_session_url = None
                export.completed_at = now
                continue
            export.status = DriveExportStatus.queued
            export.queued_at = now
            resend.append(str(export.id))
        db.commit()
    for export_id in resend:
        _enqueue(export_id)
    return len(resend)
