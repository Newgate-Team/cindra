from app import drive_exports
from app.celery_app import celery_app
from app.tool_integrations.google_drive import DriveTransientError


@celery_app.task(
    bind=True,
    autoretry_for=(DriveTransientError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_kwargs={"max_retries": drive_exports.MAX_ATTEMPTS - 1},
)
def export_to_drive(self, export_id: str) -> None:
    drive_exports.run_export(
        export_id, final_attempt=self.request.retries >= drive_exports.MAX_ATTEMPTS - 1
    )


@celery_app.task
def sweep_drive_exports() -> int:
    return drive_exports.sweep()
