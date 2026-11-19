import contextlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import Post, PostStatus, SocialPlatform, User
from app.scheduler import registry
from app.scheduler.registry import register_publisher
from app.scheduler.tasks import enqueue_due_posts, publish_post
from app.social_accounts import upsert_social_account
from app.social_integrations import linkedin, reddit, twitter, youtube
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


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """SMTP "configured", with sent mail captured instead of delivered."""
    from app.config import get_settings
    from app.scheduler import tasks

    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.example.com")
    monkeypatch.setattr(tasks, "send_email", lambda to, subject, body: sent.append((to, subject, body)))
    return sent


def test_author_is_emailed_when_a_post_fails_for_good(
    db: Session, user: User, outbox: list[tuple[str, str, str]]
) -> None:
    def _forbidden(account, post):
        raise PermanentPublishError("бот не админ канала")

    register_publisher(SocialPlatform.telegram, _forbidden)
    post = _make_post(db, user, text="Пост про кофе")

    publish_post.apply(args=[str(post.id)])

    assert len(outbox) == 1
    to, subject, body = outbox[0]
    assert to == user.email
    assert "не вышла" in subject
    assert "бот не админ канала" in body and "Пост про кофе" in body
    assert body.rstrip().endswith("/calendar")


def test_no_email_while_a_transient_failure_is_still_retrying(
    db: Session, user: User, outbox: list[tuple[str, str, str]]
) -> None:
    from celery.exceptions import Retry

    def _flaky(account, post):
        raise TransientPublishError("flood control")

    register_publisher(SocialPlatform.telegram, _flaky)
    post = _make_post(db, user)
    with pytest.raises(Retry):
        publish_post.apply(args=[str(post.id)])
    assert outbox == []

    # ...but the last allowed attempt gives up, and that one is reported.
    with pytest.raises(TransientPublishError):
        publish_post.apply(args=[str(post.id)], retries=3)
    assert len(outbox) == 1


def test_a_broken_mail_relay_does_not_change_the_failed_post(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings
    from app.scheduler import tasks

    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.example.com")

    def _relay_down(to, subject, body):
        raise ConnectionError("relay down")

    monkeypatch.setattr(tasks, "send_email", _relay_down)

    def _forbidden(account, post):
        raise PermanentPublishError("бот не админ канала")

    register_publisher(SocialPlatform.telegram, _forbidden)
    post = _make_post(db, user)

    # Eager mode may surface the relay error; what matters is the post.
    with contextlib.suppress(Exception):
        publish_post.apply(args=[str(post.id)])
    db.refresh(post)
    assert post.status == PostStatus.failed
    assert post.error_message == "бот не админ канала"


def test_no_email_without_smtp(db: Session, user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from app.scheduler import tasks

    monkeypatch.setattr(get_settings(), "smtp_host", "")
    called: list[str] = []
    monkeypatch.setattr(tasks, "send_email", lambda *a: called.append("sent"))

    def _forbidden(account, post):
        raise PermanentPublishError("бот не админ канала")

    register_publisher(SocialPlatform.telegram, _forbidden)
    publish_post.apply(args=[str(_make_post(db, user).id)])
    assert called == []


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


def test_transient_error_beyond_max_retries_gives_up(db: Session, user: User) -> None:
    # Same fix, same reason, as
    # test_content_pipeline_tasks.py::test_transient_error_beyond_max_retries_gives_up
    # (CIN-94) -- simulates the worker's state on what would be the 4th
    # delivery (initial attempt + 3 retries already made) via the
    # matching `retries` request option `apply()` accepts. Without the
    # exhausted-retries check, this post would stay "publishing"
    # forever with no error_message once Celery gives up.
    def _always_flood_limited(account, post):
        raise TransientPublishError("flood control")

    register_publisher(SocialPlatform.telegram, _always_flood_limited)
    post = _make_post(db, user)

    with pytest.raises(TransientPublishError):
        publish_post.apply(args=[str(post.id)], retries=3)

    db.refresh(post)
    assert post.status == PostStatus.failed
    assert post.error_message == "flood control"
    assert post.attempts == 1  # this call's own single execution


def test_unregistered_platform_fails_cleanly(db: Session, user: User) -> None:
    registry._REGISTRY.pop(SocialPlatform.telegram, None)
    post = _make_post(db, user)

    publish_post.apply(args=[str(post.id)])

    db.refresh(post)
    assert post.status == PostStatus.failed
    assert "telegram" in post.error_message.lower()


def test_bootstrap_registers_a_publisher_for_every_platform() -> None:
    # Cheap, direct check that adding a platform to the SocialPlatform
    # enum was actually wired into app/bootstrap.py -- catches a
    # forgotten register_publisher() call immediately instead of only
    # at actual publish time (see test_unregistered_platform_fails_cleanly
    # for what that failure mode looks like).
    assert registry.get_publisher(SocialPlatform.youtube) is youtube.publish
    assert registry.get_publisher(SocialPlatform.linkedin) is linkedin.publish
    assert registry.get_publisher(SocialPlatform.reddit) is reddit.publish
    assert registry.get_publisher(SocialPlatform.twitter) is twitter.publish


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
