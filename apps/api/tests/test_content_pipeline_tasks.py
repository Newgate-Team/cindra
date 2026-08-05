import uuid

import pytest
from celery.exceptions import Retry
from sqlalchemy.orm import Session

from app.content_pipeline import registry
from app.content_pipeline.errors import ContentModeratedError, TransientGenerationError
from app.content_pipeline.registry import register_generator
from app.content_pipeline.tasks import run_generation_job
from app.models import GenerationContentType, GenerationJob, GenerationStatus, User


@pytest.fixture(autouse=True)
def _restore_text_generator():
    previous = registry._REGISTRY.get(GenerationContentType.text)
    yield
    if previous is not None:
        register_generator(GenerationContentType.text, previous)
    else:
        registry._REGISTRY.pop(GenerationContentType.text, None)


def _create_job(db: Session, user: User) -> GenerationJob:
    job = GenerationJob(
        user_id=user.id,
        content_type=GenerationContentType.text,
        input_payload={"topic": "утренний кофе"},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_successful_generation_marks_job_completed(db: Session, user: User) -> None:
    register_generator(GenerationContentType.text, lambda payload: {"text": "Готовый пост"})
    job = _create_job(db, user)

    run_generation_job.apply(args=[str(job.id)])

    db.refresh(job)
    assert job.status == GenerationStatus.completed
    assert job.output_payload == {"text": "Готовый пост"}
    assert job.attempts == 1
    assert job.completed_at is not None


def test_permanent_failure_marks_job_failed(db: Session, user: User) -> None:
    def _boom(payload: dict) -> dict:
        raise ValueError("модель недоступна")

    register_generator(GenerationContentType.text, _boom)
    job = _create_job(db, user)

    run_generation_job.apply(args=[str(job.id)])

    db.refresh(job)
    assert job.status == GenerationStatus.failed
    assert job.error_message == "модель недоступна"


def test_moderated_content_marks_job_flagged(db: Session, user: User) -> None:
    def _rejected(payload: dict) -> dict:
        raise ContentModeratedError("упоминание конкурента")

    register_generator(GenerationContentType.text, _rejected)
    job = _create_job(db, user)

    run_generation_job.apply(args=[str(job.id)])

    db.refresh(job)
    assert job.status == GenerationStatus.flagged
    assert job.error_message == "упоминание конкурента"


def test_transient_error_triggers_a_retry(db: Session, user: User) -> None:
    # A single execution attempt: the retry loop itself (re-queue,
    # backoff, next delivery) is the Celery worker's job, not ours --
    # not something to re-verify in a unit test. What's ours to prove
    # is that a TransientGenerationError actually reaches
    # autoretry_for (raises celery.exceptions.Retry rather than
    # falling through to the generic failure branch) and that the
    # attempt is durably recorded before that happens.
    def _flaky(payload: dict) -> dict:
        raise TransientGenerationError("rate limited")

    register_generator(GenerationContentType.text, _flaky)
    job = _create_job(db, user)

    with pytest.raises(Retry):
        run_generation_job.apply(args=[str(job.id)])

    db.refresh(job)
    assert job.status == GenerationStatus.processing
    assert job.attempts == 1
    assert job.error_message is None


def test_transient_error_beyond_max_retries_gives_up(db: Session, user: User) -> None:
    # Simulates the worker's state on what would be the 4th delivery
    # (initial attempt + 3 retries already made) by passing the
    # matching `retries` request option `apply()` accepts -- Celery's
    # own retry-count bookkeeping across real re-deliveries isn't
    # reproducible from a single in-process call, but the max_retries
    # boundary this configures is. Once exceeded, autoretry_for
    # re-raises the original exception rather than a generic
    # MaxRetriesExceededError (that only happens when the exception
    # isn't known -- here it is, since it's what triggered the retry).
    #
    # On this last attempt the job must resolve to `failed` rather
    # than being left in `processing` forever -- Celery's wrapper
    # won't call run_generation_job again, so this is the only chance
    # to record that the job actually gave up (see CIN-94: found live
    # while verifying the network-timeout retry fix -- a job that
    # exhausts its retry budget was staying "processing" indefinitely
    # with no error_message, polled forever by the frontend).
    def _always_flaky(payload: dict) -> dict:
        raise TransientGenerationError("всегда недоступен")

    register_generator(GenerationContentType.text, _always_flaky)
    job = _create_job(db, user)

    with pytest.raises(TransientGenerationError):
        run_generation_job.apply(args=[str(job.id)], retries=3)

    db.refresh(job)
    assert job.status == GenerationStatus.failed
    assert job.error_message == "всегда недоступен"
    assert job.completed_at is not None
    assert job.attempts == 1  # this call's own single execution


def test_generated_text_is_moderated_automatically(db: Session, user: User) -> None:
    # The generator itself doesn't know about moderation -- it's the
    # task pipeline's job to check `output["text"]` before marking a
    # job completed, so a generator that never heard of
    # ContentModeratedError still gets flagged for bad output.
    register_generator(
        GenerationContentType.text, lambda payload: {"text": "это сука хороший кофе"}
    )
    job = _create_job(db, user)

    run_generation_job.apply(args=[str(job.id)])

    db.refresh(job)
    assert job.status == GenerationStatus.flagged
    assert "нецензурн" in job.error_message.lower()


def test_unknown_job_id_is_a_noop() -> None:
    run_generation_job.apply(args=[str(uuid.uuid4())])
