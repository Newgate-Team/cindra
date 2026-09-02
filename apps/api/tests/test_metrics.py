from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.metrics import (
    average_time_to_first_post_seconds,
    publish_success_rate,
    retention,
    time_to_first_post_seconds,
)
from app.models import Post, PostStatus, SocialPlatform, User
from app.social_accounts import upsert_social_account


def _make_user(db: Session, email: str, created_at: datetime) -> User:
    user = User(email=email, hashed_password="x", created_at=created_at)
    db.add(user)
    db.flush()
    return user


def _make_post(
    db: Session, user: User, status: PostStatus, created_at: datetime, published_at=None
) -> Post:
    account = upsert_social_account(
        db, user, SocialPlatform.telegram, f"chat-{user.id}", access_token="t"
    )
    post = Post(
        user_id=user.id,
        social_account_id=account.id,
        text="тест",
        status=status,
        scheduled_for=created_at,
        created_at=created_at,
        published_at=published_at,
    )
    db.add(post)
    db.flush()
    return post


def test_time_to_first_post_seconds_none_when_never_published(db: Session, user: User) -> None:
    assert time_to_first_post_seconds(db, user) is None


def test_time_to_first_post_seconds_uses_earliest_published_post(db: Session) -> None:
    registered_at = datetime.now(UTC) - timedelta(days=10)
    published_user = _make_user(db, "published@cindra.dev", registered_at)
    _make_post(
        db,
        published_user,
        PostStatus.published,
        registered_at + timedelta(hours=2),
        published_at=registered_at + timedelta(hours=2),
    )
    # A later post shouldn't override the *first* one.
    _make_post(
        db,
        published_user,
        PostStatus.published,
        registered_at + timedelta(hours=5),
        published_at=registered_at + timedelta(hours=5),
    )
    db.commit()

    seconds = time_to_first_post_seconds(db, published_user)
    assert seconds == timedelta(hours=2).total_seconds()


def test_average_time_to_first_post_seconds(db: Session) -> None:
    now = datetime.now(UTC) - timedelta(days=10)

    fast_user = _make_user(db, "fast@cindra.dev", now)
    _make_post(db, fast_user, PostStatus.published, now, published_at=now + timedelta(hours=1))

    slow_user = _make_user(db, "slow@cindra.dev", now)
    _make_post(db, slow_user, PostStatus.published, now, published_at=now + timedelta(hours=3))

    never_posted_user = _make_user(db, "never@cindra.dev", now)
    db.commit()

    average = average_time_to_first_post_seconds(db)
    assert average == timedelta(hours=2).total_seconds()
    assert never_posted_user.email == "never@cindra.dev"  # excluded, not counted as 0


def test_average_time_to_first_post_seconds_none_when_nobody_published(
    db: Session, user: User
) -> None:
    assert average_time_to_first_post_seconds(db) is None


def test_retention_counts_users_who_published_after_the_window(db: Session) -> None:
    registered_40_days_ago = datetime.now(UTC) - timedelta(days=40)
    retained_user = _make_user(db, "retained@cindra.dev", registered_40_days_ago)
    _make_post(
        db,
        retained_user,
        PostStatus.published,
        registered_40_days_ago + timedelta(days=35),
        published_at=registered_40_days_ago + timedelta(days=35),
    )

    churned_user = _make_user(db, "churned@cindra.dev", registered_40_days_ago)
    _make_post(
        db,
        churned_user,
        PostStatus.published,
        registered_40_days_ago + timedelta(days=2),
        published_at=registered_40_days_ago + timedelta(days=2),
    )
    db.commit()

    assert retention(db, days=30) == 0.5  # 1 of 2 posted again after day 30


def test_retention_excludes_users_too_new_for_the_window(db: Session) -> None:
    registered_yesterday = datetime.now(UTC) - timedelta(days=1)
    _make_user(db, "toonew@cindra.dev", registered_yesterday)
    db.commit()

    # Not old enough to be judged on a 30-day window yet -- shouldn't
    # count as churned (denominator 0, not "0/1").
    assert retention(db, days=30) == 0.0


def test_publish_success_rate_ignores_in_flight_posts(db: Session, user: User) -> None:
    now = datetime.now(UTC)
    _make_post(db, user, PostStatus.published, now, published_at=now)
    _make_post(db, user, PostStatus.published, now, published_at=now)
    _make_post(db, user, PostStatus.failed, now)
    _make_post(db, user, PostStatus.scheduled, now + timedelta(hours=1))
    db.commit()

    # 2 published, 1 failed, 1 still scheduled (excluded) -> 2/3
    assert publish_success_rate(db) == 2 / 3


def test_publish_success_rate_none_when_no_terminal_posts(db: Session, user: User) -> None:
    _make_post(db, user, PostStatus.scheduled, datetime.now(UTC) + timedelta(hours=1))
    assert publish_success_rate(db) is None


def _admin_token(client: TestClient, db: Session, email: str) -> str:
    payload = {"email": email, "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    user = db.scalar(select(User).where(User.email == email))
    # is_admin is set by hand in the DB (CIN-147) -- no API grants it.
    user.is_admin = True
    db.commit()
    return client.post("/auth/login", json=payload).json()["access_token"]


def test_metrics_summary_endpoint_with_no_data(client: TestClient, db: Session) -> None:
    token = _admin_token(client, db, "ada@cindra.dev")

    response = client.get(
        "/metrics/summary", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["average_time_to_first_post_seconds"] is None
    assert body["retention_d7"] == 0.0
    assert body["retention_d30"] == 0.0
    assert body["publish_success_rate"] is None


def test_metrics_summary_requires_auth(client: TestClient) -> None:
    assert client.get("/metrics/summary").status_code == 401


def test_metrics_summary_forbidden_for_ordinary_user(client: TestClient) -> None:
    # CIN-147: registration is open and unverified, so plain
    # authentication used to hand the whole user base's retention and
    # conversion to anyone who signed up.
    payload = {"email": "outsider@example.com", "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    token = client.post("/auth/login", json=payload).json()["access_token"]

    response = client.get(
        "/metrics/summary", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403


def test_admin_cannot_be_self_assigned_through_the_api(
    client: TestClient, db: Session
) -> None:
    # CIN-147: `role` is user-supplied at registration and editable in
    # the profile, which is exactly why staff access hangs off a
    # separate column. Neither entry point may set it.
    email = "climber@example.com"
    password = "supersecret1"
    client.post(
        "/auth/register",
        json={"email": email, "password": password, "role": "agency", "is_admin": True},
    )
    token = client.post(
        "/auth/login", json={"email": email, "password": password}
    ).json()["access_token"]
    client.patch(
        "/auth/me",
        json={"role": "agency", "is_admin": True},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert db.scalar(select(User).where(User.email == email)).is_admin is False
    assert (
        client.get(
            "/metrics/summary", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 403
    )
