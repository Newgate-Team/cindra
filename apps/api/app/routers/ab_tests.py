from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content_pipeline.tasks import run_generation_job
from app.db import get_db
from app.deps import get_current_user
from app.models import (
    ABTest,
    GenerationContentType,
    GenerationJob,
    UsageEventType,
    User,
)
from app.schemas import ABTestCreate, ABTestOut, ABTestWinner
from app.usage import enforce_and_record_usage_bulk

router = APIRouter(prefix="/ab-tests", tags=["ab-tests"])


def _variants(db: Session, test_id) -> list[GenerationJob]:
    return list(
        db.scalars(
            select(GenerationJob)
            .where(GenerationJob.ab_test_id == test_id)
            .order_by(GenerationJob.created_at)
        )
    )


def _to_out(test: ABTest, variants: list[GenerationJob]) -> ABTestOut:
    return ABTestOut(
        id=test.id,
        topic=test.topic,
        winner_generation_job_id=test.winner_generation_job_id,
        variants=variants,
        created_at=test.created_at,
    )


@router.post("", response_model=ABTestOut, status_code=status.HTTP_201_CREATED)
def create_ab_test(
    payload: ABTestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ABTestOut:
    """Kick off `variant_count` independent text generations for the
    same brief -- each one is a completely ordinary GenerationJob
    (content_type=text), just tagged to this test, and runs through
    the exact same Celery pipeline/generator a standalone text
    generation would. See ABTest's own docstring for why there's no
    automated winner: nothing in this codebase reads back platform
    engagement data to judge variants against.
    """
    # Bulk, all-or-nothing (CIN-106's fan-out precedent): either the
    # whole batch fits this period's text-generation quota, or none of
    # it is recorded/started, rather than starting some prefix of the
    # requested variants and silently dropping the rest.
    enforce_and_record_usage_bulk(
        db,
        current_user,
        UsageEventType.generation,
        count=payload.variant_count,
        content_type=GenerationContentType.text,
    )

    test = ABTest(user_id=current_user.id, topic=payload.topic)
    db.add(test)
    db.flush()

    input_payload = {
        "topic": payload.topic,
        "content_kind": payload.content_kind,
        "brand_guide": payload.brand_guide,
        "tone": payload.tone,
    }
    variants = []
    for _ in range(payload.variant_count):
        job = GenerationJob(
            user_id=current_user.id,
            content_type=GenerationContentType.text,
            input_payload=input_payload,
            ab_test_id=test.id,
        )
        db.add(job)
        variants.append(job)
    db.commit()

    for job in variants:
        run_generation_job.delay(str(job.id))
    # Same reasoning as content.py::generate_content -- in tests,
    # task_always_eager runs each job synchronously on a separate DB
    # session, so this session's copy is stale until re-queried.
    for job in variants:
        db.refresh(job)

    return _to_out(test, variants)


@router.get("/{test_id}", response_model=ABTestOut)
def get_ab_test(
    test_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ABTestOut:
    test = db.get(ABTest, test_id)
    if test is None or test.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тест не найден")
    return _to_out(test, _variants(db, test.id))


@router.post("/{test_id}/winner", response_model=ABTestOut)
def set_ab_test_winner(
    test_id: str,
    payload: ABTestWinner,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ABTestOut:
    test = db.get(ABTest, test_id)
    if test is None or test.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тест не найден")

    variants = _variants(db, test.id)
    if not any(job.id == payload.generation_job_id for job in variants):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Этот вариант не принадлежит данному тесту",
        )

    test.winner_generation_job_id = payload.generation_job_id
    db.commit()
    return _to_out(test, variants)
