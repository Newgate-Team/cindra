from datetime import UTC, datetime

from app.celery_app import celery_app
from app.db import SessionLocal
from app.models import Post, PostStatus, SocialAccount
from app.scheduler.registry import get_publisher
from app.social_integrations.errors import PermanentPublishError, TransientPublishError


@celery_app.task(
    bind=True,
    autoretry_for=(TransientPublishError,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_kwargs={"max_retries": 3},
)
def publish_post(self, post_id: str) -> None:
    with SessionLocal() as db:
        post = db.get(Post, post_id)
        if post is None:
            return

        account = db.get(SocialAccount, post.social_account_id)

        post.status = PostStatus.publishing
        post.attempts += 1
        db.commit()

        try:
            publisher = get_publisher(account.platform)
            result = publisher(account, post.text)
        except (PermanentPublishError, NotImplementedError) as exc:
            post.status = PostStatus.failed
            post.error_message = str(exc)
            db.commit()
            return
        except TransientPublishError:
            # Persist the attempt count now -- Celery re-raises this
            # to trigger autoretry (same pattern as
            # content_pipeline.tasks.run_generation_job).
            db.commit()
            raise
        except Exception as exc:  # noqa: BLE001 -- adapter failures fail the post, not the worker
            post.status = PostStatus.failed
            post.error_message = str(exc)
            db.commit()
            return

        post.status = PostStatus.published
        post.platform_message_id = str(result.get("message_id") or result.get("id") or "")
        post.published_at = datetime.now(UTC)
        db.commit()


@celery_app.task
def enqueue_due_posts() -> int:
    """Periodic task (Celery beat): find scheduled posts whose time has
    come and hand them to publish_post. Runs every minute -- see
    celery_app.conf.beat_schedule.

    Flips status to `publishing` *before* dispatching so a beat tick
    that overlaps with a still-running previous tick (e.g. a slow beat
    scheduler) can't double-enqueue the same post; publish_post sets
    the same status itself once it actually starts, this just claims
    the row first.
    """
    with SessionLocal() as db:
        due_posts = list(
            db.query(Post)
            .filter(
                Post.status == PostStatus.scheduled,
                Post.scheduled_for <= datetime.now(UTC),
            )
            .all()
        )
        for post in due_posts:
            post.status = PostStatus.publishing
        db.commit()

        for post in due_posts:
            publish_post.delay(str(post.id))

        return len(due_posts)
