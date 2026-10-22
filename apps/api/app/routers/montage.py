"""Montage jobs: assembled and rendered in DaVinci Resolve.

Resolve runs on a separate render machine (Studio edition -- the free
one can't be scripted), so the work is pulled, not pushed: a
davinci_agent there claims queued jobs, reports progress and uploads
the finished video through /montage/agent/*, authenticated by a shared
MONTAGE_AGENT_TOKEN. Users only ever touch /montage/jobs.
"""

import hmac
import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content_pipeline.attachments import AttachmentTooLargeError, read_upload_capped
from app.content_pipeline.media_storage import upload_bytes
from app.db import get_db
from app.deps import get_current_user
from app.drive_exports import queue_export
from app.models import (
    MontageAgent,
    MontageJob,
    MontageStatus,
    UsageEvent,
    UsageEventType,
    User,
)
from app.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page, paginate
from app.schemas import (
    MontageAgentFailure,
    MontageAgentHello,
    MontageAgentJobOut,
    MontageAgentProgress,
    MontageAgentsStatusOut,
    MontageCreate,
    MontageJobOut,
    MontageMediaOut,
)
from app.social_integrations.media_validation import is_own_media_url
from app.teams import visible_user_ids
from app.usage import check_usage_limit

router = APIRouter(prefix="/montage", tags=["montage"])
log = logging.getLogger(__name__)

_SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
_FPS = 25
_DEFAULT_STILL_SECONDS = 3.0
# An agent reports progress every few seconds while it works; a job
# silent this long means the agent (or its machine) died mid-render and
# the job goes back to the queue for another attempt.
_STALE_AFTER = timedelta(minutes=3)
_MAX_ATTEMPTS = 3
_ONLINE_WINDOW = timedelta(minutes=2)
_MAX_RESULT_BYTES = 500 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(UTC)


def _visible_job(db: Session, job_id: uuid.UUID, user: User) -> MontageJob:
    job = db.get(MontageJob, job_id)
    if job is None or job.user_id not in visible_user_ids(db, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Монтаж не найден")
    return job


@router.post("/jobs", response_model=MontageJobOut, status_code=status.HTTP_201_CREATED)
def create_montage(
    payload: MontageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MontageJob:
    # The agent downloads every URL in the spec onto the render
    # machine, so only Cindra's own media bucket is allowed -- otherwise
    # a montage becomes a way to make that machine fetch arbitrary URLs.
    urls = [clip.url for clip in payload.clips] + ([payload.music.url] if payload.music else [])
    foreign = [url for url in urls if not is_own_media_url(url)]
    if foreign:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Монтировать можно только файлы, загруженные или созданные в Cindra",
        )

    width, height = _SIZES[payload.aspect_ratio]
    spec = {
        "version": 1,
        "width": width,
        "height": height,
        "fps": _FPS,
        "clips": [
            {
                "type": clip.type,
                "url": clip.url,
                "name": clip.name or f"Клип {index}",
                "duration_seconds": (
                    (clip.duration_seconds or _DEFAULT_STILL_SECONDS)
                    if clip.type == "image"
                    else None
                ),
            }
            for index, clip in enumerate(payload.clips, start=1)
        ],
        "music": payload.music.model_dump() if payload.music else None,
        "subtitles": payload.subtitles,
        "subtitles_language": payload.subtitles_language,
    }

    # Same one-transaction check/record/create as the HeyGen batch: the
    # usage row and the job commit together (CIN-158 lock only spans
    # local DB work).
    check_usage_limit(db, current_user, UsageEventType.davinci_montage, for_update=True)
    job = MontageJob(
        user_id=current_user.id,
        status=MontageStatus.queued,
        title=payload.title,
        spec=spec,
        stage="В очереди на монтаж",
    )
    db.add(job)
    db.add(UsageEvent(user_id=current_user.id, event_type=UsageEventType.davinci_montage))
    db.commit()
    db.refresh(job)
    return job


# Exactly what davinci_agent/media_probe.py can measure -- anything else
# would only fail later, on the render machine.
_MEDIA_TYPES: dict[str, tuple[str, str, int]] = {
    "video/mp4": ("video", "mp4", 200 * 1024 * 1024),
    "video/quicktime": ("video", "mov", 200 * 1024 * 1024),
    "image/png": ("image", "png", 20 * 1024 * 1024),
    "image/jpeg": ("image", "jpg", 20 * 1024 * 1024),
    "image/webp": ("image", "webp", 20 * 1024 * 1024),
    "audio/mpeg": ("audio", "mp3", 30 * 1024 * 1024),
    "audio/mp3": ("audio", "mp3", 30 * 1024 * 1024),
    "audio/wav": ("audio", "wav", 30 * 1024 * 1024),
    "audio/x-wav": ("audio", "wav", 30 * 1024 * 1024),
    "audio/mp4": ("audio", "m4a", 30 * 1024 * 1024),
}


@router.post("/media", response_model=MontageMediaOut, status_code=status.HTTP_201_CREATED)
async def upload_montage_media(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
) -> MontageMediaOut:
    """Stored as-is: /content/attachment shrinks images for Gemini,
    which would leave B-roll blurry in a 1080x1920 cut."""
    mime = (file.content_type or "").split(";")[0].strip()
    if mime not in _MEDIA_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Для монтажа подходят MP4/MOV, PNG/JPEG/WebP и MP3/WAV/M4A",
        )
    kind, extension, max_bytes = _MEDIA_TYPES[mime]
    try:
        data = await read_upload_capped(file, max_bytes)
    except AttachmentTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from None
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пустой файл")
    return MontageMediaOut(url=upload_bytes(data, mime, extension), type=kind)


@router.get("/jobs", response_model=Page[MontageJobOut])
def list_montages(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Page[MontageJobOut]:
    query = (
        select(MontageJob)
        .where(MontageJob.user_id.in_(visible_user_ids(db, current_user)))
        .order_by(MontageJob.created_at.desc())
    )
    rows, total = paginate(db, query, limit, offset)
    return Page[MontageJobOut](
        items=[MontageJobOut.model_validate(job) for (job,) in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/{job_id}", response_model=MontageJobOut)
def get_montage(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MontageJob:
    return _visible_job(db, job_id, current_user)


@router.get("/agents/status", response_model=MontageAgentsStatusOut)
def agents_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MontageAgentsStatusOut:
    last_seen = db.scalar(select(func.max(MontageAgent.last_seen_at)))
    return MontageAgentsStatusOut(
        online=last_seen is not None and last_seen > _now() - _ONLINE_WINDOW,
        last_seen_at=last_seen,
    )


def require_agent(authorization: str | None = Header(default=None)) -> None:
    expected = get_settings().montage_agent_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DaVinci-агент не настроен на сервере (MONTAGE_AGENT_TOKEN)",
        )
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный токен агента")


def _touch_agent(db: Session, hello: MontageAgentHello) -> None:
    agent = db.get(MontageAgent, hello.agent_name)
    if agent is None:
        db.add(MontageAgent(name=hello.agent_name, version=hello.version, last_seen_at=_now()))
    else:
        agent.last_seen_at = _now()
        if hello.version:
            agent.version = hello.version


def _agents_job(db: Session, job_id: uuid.UUID, agent_name: str) -> MontageJob:
    job = db.get(MontageJob, job_id, with_for_update=True, populate_existing=True)
    if job is None or job.status != MontageStatus.processing or job.agent_name != agent_name:
        # Reassigned after this agent went quiet past _STALE_AFTER, or
        # already finished -- the agent must drop it, not overwrite.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Задача больше не закреплена за агентом"
        )
    return job


@router.post("/agent/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
def agent_heartbeat(
    hello: MontageAgentHello,
    _: None = Depends(require_agent),
    db: Session = Depends(get_db),
) -> Response:
    _touch_agent(db, hello)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/agent/claim", response_model=MontageAgentJobOut)
def agent_claim(
    hello: MontageAgentHello,
    _: None = Depends(require_agent),
    db: Session = Depends(get_db),
) -> MontageAgentJobOut | Response:
    _touch_agent(db, hello)
    while True:
        # SKIP LOCKED: two agents claiming at once each get a different
        # job instead of one waiting on (then double-taking) the other's.
        job = db.scalar(
            select(MontageJob)
            .where(
                or_(
                    MontageJob.status == MontageStatus.queued,
                    and_(
                        MontageJob.status == MontageStatus.processing,
                        MontageJob.heartbeat_at < _now() - _STALE_AFTER,
                    ),
                )
            )
            .order_by(MontageJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            db.commit()
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        if job.attempts >= _MAX_ATTEMPTS:
            # Whatever keeps killing the agent mid-job, retrying it
            # forever would block the queue behind it.
            job.status = MontageStatus.failed
            job.error_message = (
                f"DaVinci не смог завершить монтаж за {_MAX_ATTEMPTS} попытки — "
                "попробуйте ещё раз или измените состав клипов"
            )
            job.completed_at = _now()
            db.commit()
            continue
        job.status = MontageStatus.processing
        job.agent_name = hello.agent_name
        job.attempts += 1
        job.claimed_at = job.heartbeat_at = _now()
        job.stage = "DaVinci взял задачу"
        job.progress = 0
        db.commit()
        return MontageAgentJobOut(id=job.id, spec=job.spec, title=job.title)


@router.post("/agent/jobs/{job_id}/progress", status_code=status.HTTP_204_NO_CONTENT)
def agent_progress(
    job_id: uuid.UUID,
    update: MontageAgentProgress,
    _: None = Depends(require_agent),
    db: Session = Depends(get_db),
) -> Response:
    job = _agents_job(db, job_id, update.agent_name)
    job.stage = update.stage
    job.progress = update.progress
    job.heartbeat_at = _now()
    _touch_agent(db, MontageAgentHello(agent_name=update.agent_name))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/agent/jobs/{job_id}/result", status_code=status.HTTP_204_NO_CONTENT)
async def agent_result(
    job_id: uuid.UUID,
    file: UploadFile,
    agent_name: str = Query(min_length=1, max_length=120),
    _: None = Depends(require_agent),
    db: Session = Depends(get_db),
) -> Response:
    _agents_job(db, job_id, agent_name)
    db.commit()  # don't hold the row lock through a big upload
    if (file.content_type or "").split(";")[0].strip() != "video/mp4":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ожидается video/mp4")
    try:
        data = await read_upload_capped(file, _MAX_RESULT_BYTES)
    except AttachmentTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from None
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пустой файл")
    url = upload_bytes(data, "video/mp4", "mp4")

    job = _agents_job(db, job_id, agent_name)
    job.status = MontageStatus.completed
    job.result_video_url = url
    job.progress = 100
    job.stage = "Готово"
    job.completed_at = _now()
    db.commit()
    owner = db.get(User, job.user_id)
    if owner is not None:
        try:
            queue_export(
                db,
                owner,
                url=url,
                label="Монтаж",
                title=job.title,
                origin="montage",
            )
        except Exception:  # the montage is done; only the Drive copy is lost
            db.rollback()
            log.exception("drive auto-save failed to queue for montage %s", job.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/agent/jobs/{job_id}/fail", status_code=status.HTTP_204_NO_CONTENT)
def agent_fail(
    job_id: uuid.UUID,
    failure: MontageAgentFailure,
    _: None = Depends(require_agent),
    db: Session = Depends(get_db),
) -> Response:
    job = _agents_job(db, job_id, failure.agent_name)
    job.status = MontageStatus.failed
    job.error_message = failure.error[:2000]
    job.completed_at = _now()
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
