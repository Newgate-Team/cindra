from celery import Celery

from app.config import get_settings

celery_app = Celery(
    "cindra",
    broker=get_settings().redis_url,
    backend=get_settings().redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_default_retry_delay=10,
    task_acks_late=True,
    beat_schedule={
        "enqueue-due-posts": {
            "task": "app.scheduler.tasks.enqueue_due_posts",
            "schedule": 60.0,
        },
    },
)

celery_app.autodiscover_tasks(["app.content_pipeline", "app.scheduler"])
