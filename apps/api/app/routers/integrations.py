import uuid
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content_pipeline.errors import ContentModeratedError
from app.content_pipeline.moderation import check_content
from app.content_pipeline.tasks import run_generation_job
from app.db import get_db
from app.deps import get_current_user
from app.integrations import (
    find_integration,
    get_secret,
    integration_out,
    upsert_integration,
)
from app.models import (
    GenerationContentType,
    GenerationJob,
    GenerationStatus,
    IntegrationProvider,
    UsageEvent,
    UsageEventType,
    User,
)
from app.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page, paginate
from app.schemas import (
    HeyGenAccountOut,
    HeyGenAvatarPage,
    HeyGenConnectRequest,
    HeyGenVideoJobOut,
    HeyGenVideoRequest,
    HeyGenVoicePage,
    IntegrationOut,
)
from app.teams import visible_user_ids
from app.tool_integrations import heygen
from app.usage import check_usage_limit

router = APIRouter(prefix="/integrations", tags=["integrations"])


_STORED_KEY_REJECTED = "Ключ HeyGen недействителен или отозван — подключите HeyGen заново"


def _call_heygen[T](fn: Callable[[], T], auth_message: str = _STORED_KEY_REJECTED) -> T:
    try:
        return fn()
    except heygen.HeyGenAuthError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=auth_message
        ) from None
    except heygen.HeyGenTransientError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except heygen.HeyGenError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


def _heygen_key(db: Session, user: User) -> str:
    integration = find_integration(db, user, IntegrationProvider.heygen)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="HeyGen не подключён"
        )
    return get_secret(integration)


@router.get("", response_model=list[IntegrationOut])
def list_integrations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[IntegrationOut]:
    found = (find_integration(db, current_user, provider) for provider in IntegrationProvider)
    return [integration_out(integration, current_user) for integration in found if integration]


@router.put("/heygen", response_model=IntegrationOut)
def connect_heygen(
    payload: HeyGenConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IntegrationOut:
    # Validated against HeyGen before it's stored, so a typo surfaces
    # here, on the connect form, instead of as a failed video later.
    account = _call_heygen(
        lambda: heygen.get_current_user(payload.api_key),
        auth_message="HeyGen не принял этот ключ — проверьте, что он скопирован целиком "
        "(HeyGen → Settings → API)",
    )
    integration = upsert_integration(
        db,
        current_user,
        IntegrationProvider.heygen,
        payload.api_key,
        heygen.account_label(account),
    )
    return integration_out(integration, current_user)


@router.delete("/heygen", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_heygen(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    integration = find_integration(db, current_user, IntegrationProvider.heygen)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="HeyGen не подключён"
        )
    db.delete(integration)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/heygen/account", response_model=HeyGenAccountOut)
def heygen_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HeyGenAccountOut:
    api_key = _heygen_key(db, current_user)
    account = _call_heygen(lambda: heygen.get_current_user(api_key))
    balance, currency = heygen.wallet_balance(account)
    return HeyGenAccountOut(
        account_label=heygen.account_label(account),
        billing_type=account.get("billing_type"),
        remaining_balance=balance,
        currency=currency,
    )


@router.get("/heygen/avatars", response_model=HeyGenAvatarPage)
def heygen_avatars(
    token: str | None = Query(default=None, max_length=512),
    ownership: Literal["public", "private"] | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HeyGenAvatarPage:
    api_key = _heygen_key(db, current_user)
    page = _call_heygen(
        lambda: heygen.list_avatar_looks(api_key, token=token, ownership=ownership)
    )
    return HeyGenAvatarPage.model_validate(page)


def _video_out(job: GenerationJob) -> HeyGenVideoJobOut:
    payload = job.input_payload or {}
    return HeyGenVideoJobOut(
        id=job.id,
        batch_id=payload.get("heygen_batch_id", ""),
        avatar_id=payload.get("avatar_id", ""),
        voice_id=payload.get("voice_id"),
        script=payload.get("script", ""),
        aspect_ratio=payload.get("aspect_ratio", ""),
        status=job.status,
        video_url=(job.output_payload or {}).get("video_url"),
        error_message=job.error_message,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.post(
    "/heygen/videos",
    response_model=list[HeyGenVideoJobOut],
    status_code=status.HTTP_201_CREATED,
)
def create_heygen_videos(
    payload: HeyGenVideoRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[HeyGenVideoJobOut]:
    """One render per variant, each its own GenerationJob, so the
    existing video pipeline (worker, R2 copy, ReviewAndPublish, team
    visibility) carries them with no special-casing."""
    integration = find_integration(db, current_user, IntegrationProvider.heygen)
    if integration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="HeyGen не подключён")

    # Moderated up front, not on the worker's output like /content: by
    # the time a worker could flag it, the user's HeyGen wallet has
    # already paid for every render.
    try:
        check_content(payload.script)
    except ContentModeratedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    count = len(payload.variants)
    # Check, record and create in one short transaction (lock held only
    # for local DB work, CIN-158) -- the usage rows and jobs commit
    # together, so a failure can't leave quota spent with no jobs.
    check_usage_limit(
        db, current_user, UsageEventType.byo_avatar_video, count=count, for_update=True
    )
    batch_id = uuid.uuid4().hex
    jobs = [
        GenerationJob(
            user_id=current_user.id,
            content_type=GenerationContentType.video,
            status=GenerationStatus.queued,
            input_payload={
                "provider": "heygen",
                "heygen_integration_id": str(integration.id),
                "heygen_batch_id": batch_id,
                # Becomes HeyGen's Idempotency-Key -- one per render,
                # stable across Celery retries of that render.
                "heygen_request_id": uuid.uuid4().hex,
                "avatar_id": variant.avatar_id,
                "voice_id": variant.voice_id,
                "script": payload.script,
                "aspect_ratio": payload.aspect_ratio,
                "title": payload.title,
            },
        )
        for variant in payload.variants
    ]
    db.add_all(jobs)
    db.add_all(
        UsageEvent(user_id=current_user.id, event_type=UsageEventType.byo_avatar_video)
        for _ in range(count)
    )
    db.commit()

    for job in jobs:
        run_generation_job.delay(str(job.id))
    for job in jobs:
        db.refresh(job)
    return [_video_out(job) for job in jobs]


@router.get("/heygen/videos", response_model=Page[HeyGenVideoJobOut])
def list_heygen_videos(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Page[HeyGenVideoJobOut]:
    query = (
        select(GenerationJob)
        .where(
            GenerationJob.user_id.in_(visible_user_ids(db, current_user)),
            GenerationJob.input_payload["provider"].astext == "heygen",
        )
        .order_by(GenerationJob.created_at.desc())
    )
    rows, total = paginate(db, query, limit, offset)
    return Page[HeyGenVideoJobOut](
        items=[_video_out(job) for (job,) in rows], total=total, limit=limit, offset=offset
    )


@router.get("/heygen/voices", response_model=HeyGenVoicePage)
def heygen_voices(
    token: str | None = Query(default=None, max_length=512),
    language: str | None = Query(default=None, max_length=64),
    gender: Literal["male", "female"] | None = None,
    voice_type: Literal["public", "private"] | None = Query(default=None, alias="type"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HeyGenVoicePage:
    api_key = _heygen_key(db, current_user)
    page = _call_heygen(
        lambda: heygen.list_voices(
            api_key, token=token, language=language, gender=gender, voice_type=voice_type
        )
    )
    return HeyGenVoicePage.model_validate(page)
