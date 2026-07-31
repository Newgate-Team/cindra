from datetime import UTC, datetime

from app.celery_app import celery_app
from app.content_pipeline.errors import ContentModeratedError, TransientGenerationError
from app.content_pipeline.registry import get_generator
from app.db import SessionLocal
from app.models import GenerationJob, GenerationStatus


@celery_app.task(
    bind=True,
    autoretry_for=(TransientGenerationError,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_kwargs={"max_retries": 3},
)
def run_generation_job(self, job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(GenerationJob, job_id)
        if job is None:
            return

        job.status = GenerationStatus.processing
        job.attempts += 1
        db.commit()

        try:
            generator = get_generator(job.content_type)
            output = generator(job.input_payload)
        except ContentModeratedError as exc:
            job.status = GenerationStatus.flagged
            job.error_message = str(exc)
            job.completed_at = datetime.now(UTC)
            db.commit()
            return
        except TransientGenerationError:
            # Persist the attempt count now -- Celery re-raises this
            # to trigger autoretry, so nothing past this point runs.
            db.commit()
            raise
        except Exception as exc:  # noqa: BLE001 -- generator failures fail the job, not the worker
            job.status = GenerationStatus.failed
            job.error_message = str(exc)
            job.completed_at = datetime.now(UTC)
            db.commit()
            return

        job.status = GenerationStatus.completed
        job.output_payload = output
        job.completed_at = datetime.now(UTC)
        db.commit()
