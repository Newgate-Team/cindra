from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content_pipeline.attachments import (
    AttachmentTooLargeError,
    UnsupportedAttachmentError,
    classify_attachment,
    downscale_image_for_context,
)
from app.content_pipeline.media_storage import upload_bytes
from app.content_pipeline.publish_matrix import (
    InvalidGenerationTargetError,
    validate_generation_target,
)
from app.content_pipeline.tasks import run_generation_job
from app.db import get_db
from app.deps import get_current_user
from app.models import GenerationJob, SocialAccount, UsageEventType, User
from app.schemas import AttachmentOut, GenerationJobOut, GenerationRequest
from app.usage import enforce_and_record_usage

router = APIRouter(prefix="/content", tags=["content"])


@router.post("/attachment", response_model=AttachmentOut, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    file: UploadFile, current_user: User = Depends(get_current_user)
) -> AttachmentOut:
    """Upload an optional context file (CIN-97) for a later /content/generate
    call -- separate from generation itself (which is async/queued) since
    upload validation and the R2 PUT are both fast, synchronous operations.
    Free on every tier: this isn't a metered UsageEvent, just storage.
    """
    data = await file.read()
    mime_type = file.content_type or "application/octet-stream"
    try:
        # Size cap is enforced on the original upload, before any
        # downscaling below -- otherwise it'd be trivial to dodge the
        # cap with an image that only becomes small after resizing.
        attachment_type = classify_attachment(mime_type, len(data))
    except UnsupportedAttachmentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    except AttachmentTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)
        ) from None

    extension = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "bin"
    if attachment_type == "image":
        # CIN-98: shrink to Gemini's single-tile bound once here, at
        # upload time, rather than on every later generation that
        # reads this attachment back.
        try:
            data, mime_type = downscale_image_for_context(data)
        except UnsupportedAttachmentError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
        extension = "jpg"
    url = upload_bytes(data, mime_type, extension)
    return AttachmentOut(url=url, attachment_type=attachment_type, mime_type=mime_type)


@router.post(
    "/generate", response_model=GenerationJobOut, status_code=status.HTTP_202_ACCEPTED
)
def generate_content(
    payload: GenerationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GenerationJob:
    # Target accounts are chosen up front (CIN-106) -- content_type/
    # content_kind must be publishable to all of them at once, checked
    # here (before spending any generation budget) rather than only
    # failing later at actual publish time.
    accounts = db.scalars(
        select(SocialAccount).where(SocialAccount.id.in_(payload.target_account_ids))
    ).all()
    found_ids = {a.id for a in accounts}
    missing = set(payload.target_account_ids) - found_ids
    if missing or any(a.user_id != current_user.id for a in accounts):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Соцаккаунт не найден"
        )

    platforms = {a.platform for a in accounts}
    try:
        validate_generation_target(platforms, payload.content_type, payload.content_kind)
    except InvalidGenerationTargetError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    # content_type defaults to text. All three content types now have
    # real generators registered (text: CIN-53, image: CIN-54, video:
    # CIN-55) -- nothing about this endpoint or the queue needed to
    # change once they were. Limit is per-format (CIN-60), not a
    # single "generations" total -- text/image/video cost four orders
    # of magnitude apart (see CIN-59).
    enforce_and_record_usage(
        db, current_user, UsageEventType.generation, payload.content_type
    )

    input_payload = payload.model_dump(mode="json")
    # Text generation's tone/format guidance is keyed by a single
    # platform (see prompts.py); image/video generation don't read
    # platform at all. Rather than the bigger scope of generating a
    # distinct text variant per target platform, tone is derived from
    # the first-selected target account.
    first_account = min(accounts, key=lambda a: payload.target_account_ids.index(a.id))
    input_payload["platform"] = first_account.platform.value

    job = GenerationJob(
        user_id=current_user.id,
        content_type=payload.content_type,
        input_payload=input_payload,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    run_generation_job.delay(str(job.id))

    # In production this dispatch is fire-and-forget (async worker,
    # job still "queued" in the response). In tests task_always_eager
    # runs it synchronously *on a separate DB session/connection*
    # inside the task, so this session's copy of `job` is stale until
    # re-queried -- refresh() always re-SELECTs regardless of prior
    # expiration state, so it picks up whatever the task committed.
    db.refresh(job)
    return job


@router.get("/{job_id}", response_model=GenerationJobOut)
def get_generation_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if job is None or job.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Задача не найдена"
        )
    return job
