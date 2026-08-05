from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.content_pipeline.attachments import (
    AttachmentTooLargeError,
    UnsupportedAttachmentError,
    classify_attachment,
)
from app.content_pipeline.media_storage import upload_bytes
from app.content_pipeline.tasks import run_generation_job
from app.db import get_db
from app.deps import get_current_user
from app.models import GenerationJob, UsageEventType, User
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
        attachment_type = classify_attachment(mime_type, len(data))
    except UnsupportedAttachmentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    except AttachmentTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)
        ) from None

    extension = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "bin"
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
    # content_type defaults to text. All three content types now have
    # real generators registered (text: CIN-53, image: CIN-54, video:
    # CIN-55) -- nothing about this endpoint or the queue needed to
    # change once they were. Limit is per-format (CIN-60), not a
    # single "generations" total -- text/image/video cost four orders
    # of magnitude apart (see CIN-59).
    enforce_and_record_usage(
        db, current_user, UsageEventType.generation, payload.content_type
    )

    job = GenerationJob(
        user_id=current_user.id,
        content_type=payload.content_type,
        input_payload=payload.model_dump(mode="json"),
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
