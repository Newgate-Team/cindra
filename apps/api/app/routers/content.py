from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.content_pipeline.tasks import run_generation_job
from app.db import get_db
from app.deps import get_current_user
from app.models import GenerationContentType, GenerationJob, User
from app.schemas import GenerationJobOut, GenerationRequest

router = APIRouter(prefix="/content", tags=["content"])


@router.post(
    "/generate", response_model=GenerationJobOut, status_code=status.HTTP_202_ACCEPTED
)
def generate_text(
    payload: GenerationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GenerationJob:
    job = GenerationJob(
        user_id=current_user.id,
        content_type=GenerationContentType.text,
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
