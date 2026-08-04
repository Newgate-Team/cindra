from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import Post, PostStatus, SocialPlatform, User
from app.scheduler import registry
from app.scheduler.registry import register_publisher
from app.scheduler.tasks import enqueue_due_posts, publish_post
from app.social_accounts import upsert_social_account
from app.social_integrations.errors import PermanentPublishError, TransientPublishError


@pytest.fixture(autouse=True)
def _restore_telegram_publisher():
    previous = registry._REGISTRY.get(SocialPlatform.telegram)
    yield
    if previous is not None:
        register_publisher(SocialPlatform.telegram, previous)
    else:
        registry._REGISTRY.pop(SocialPlatform.telegram, None)


def _make_post(db: Session, user: User, **overrides) -> Post:
    account = upsert_social_account(
        db, user, SocialPlatform.telegram, "-100123", access_token="bot-token"
    )
    post = Post(
        user_id=user.id,
        social_account_id=account.id,
        text=overrides.pop("text", "Готовый пост про кофе"),
        scheduled_for=overrides.pop("scheduled_for", datetime.now(UTC)),
        **overrides,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


def test_successful_publish_marks_post_published(db: Session, user: User) -> None:
    register_publisher(SocialPlatform.telegram, lambda account, post: {"message_id": 42})
    post = _make_post(db, user)

    publish_post.apply(args=[str(post.id)])

    db.refresh(post)
    assert post.status == PostStatus.published
    assert post.platform_message_id == "42"
    assert post.published_at is not None
    assert post.attempts == 1


def test_permanent_failure_marks_post_failed(db: Session, user: User) -> None:
    def _forbidden(account, post):
        raise PermanentPublishError("бот не админ канала")

    register_publisher(SocialPlatform.telegram, _forbidden)
    post = _make_post(db, user)

    publish_post.apply(args=[str(post.id)])

    db.refresh(post)
    assert post.status == PostStatus.failed
    assert post.error_message == "бот не админ канала"


def test_transient_error_triggers_a_retry(db: Session, user: User) -> None:
    from celery.exceptions import Retry

    def _rate_limited(account, post):
        raise TransientPublishError("flood control")

    register_publisher(SocialPlatform.telegram, _rate_limited)
    post = _make_post(db, user)

    with pytest.raises(Retry):
        publish_post.apply(args=[str(post.id)])

    db.refresh(post)
    assert post.status == PostStatus.publishing
    assert post.attempts == 1


def test_unregistered_platform_fails_cleanly(db: Session, user: User) -> None:
    registry._REGISTRY.pop(SocialPlatform.telegram, None)
    post = _make_post(db, user)

    publish_post.apply(args=[str(post.id)])

    db.refresh(post)
    assert post.status == PostStatus.failed
    assert "telegram" in post.error_message.lower()


def test_unknown_post_id_is_a_noop() -> None:
    import uuid

    publish_post.apply(args=[str(uuid.uuid4())])


def test_enqueue_due_posts_dispatches_only_due_ones(db: Session, user: User) -> None:
    register_publisher(SocialPlatform.telegram, lambda account, post: {"message_id": 1})

    due = _make_post(db, user, scheduled_for=datetime.now(UTC) - timedelta(minutes=1))
    future = _make_post(db, user, scheduled_for=datetime.now(UTC) + timedelta(hours=1))

    dispatched = enqueue_due_posts.apply().get()

    assert dispatched == 1
    db.refresh(due)
    db.refresh(future)
    assert due.status == PostStatus.published  # eager mode ran it synchronously
    assert future.status == PostStatus.scheduled
