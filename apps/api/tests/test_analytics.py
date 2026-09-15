from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import (
    daily_published_posts,
    generations_by_content_type,
    generations_overview,
    posts_by_content_kind,
    posts_by_platform,
    posts_overview,
)
from app.models import (
    GenerationContentType,
    GenerationJob,
    GenerationStatus,
    Post,
    PostStatus,
    SocialAccount,
    SocialPlatform,
    Subscription,
    UsageEvent,
    UsageEventType,
    User,
)
from app.token_crypto import encrypt_token


def _account_for(db: Session, user: User, platform: SocialPlatform) -> SocialAccount:
    # Not upsert_social_account -- that enforces CIN-155's connected-
    # account tier limit (free tier: 1), which would break tests that
    # need two platforms on one free-tier user and has nothing to do
    # with what's under test here.
    account = db.scalar(
        select(SocialAccount).where(
            SocialAccount.user_id == user.id, SocialAccount.platform == platform
        )
    )
    if account is None:
        account = SocialAccount(
            user_id=user.id,
            platform=platform,
            external_account_id=f"chat-{user.id}-{platform.value}",
            encrypted_access_token=encrypt_token("t"),
        )
        db.add(account)
        db.flush()
    return account


def _post(
    db: Session,
    user: User,
    platform: SocialPlatform,
    status: PostStatus,
    created_at: datetime,
    published_at: datetime | None = None,
    content_kind: str = "post",
) -> Post:
    account = _account_for(db, user, platform)
    post = Post(
        user_id=user.id,
        social_account_id=account.id,
        text="тест",
        content_kind=content_kind,
        status=status,
        scheduled_for=created_at,
        created_at=created_at,
        published_at=published_at,
    )
    db.add(post)
    db.flush()
    return post


def _job(
    db: Session,
    user: User,
    content_type: GenerationContentType,
    status: GenerationStatus,
    created_at: datetime,
) -> GenerationJob:
    job = GenerationJob(
        user_id=user.id,
        content_type=content_type,
        status=status,
        input_payload={"topic": "тест"},
        created_at=created_at,
    )
    db.add(job)
    db.flush()
    return job


def _auth_headers(client: TestClient, email: str = "ada@cindra.dev") -> dict:
    payload = {"email": email, "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    token = client.post("/auth/login", json=payload).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ---- pure function tests (app/analytics.py) ----


def test_posts_overview_counts_by_status_and_success_rate(db: Session, user: User) -> None:
    now = datetime.now(UTC)
    _post(db, user, SocialPlatform.telegram, PostStatus.published, now, published_at=now)
    _post(db, user, SocialPlatform.telegram, PostStatus.published, now, published_at=now)
    _post(db, user, SocialPlatform.telegram, PostStatus.failed, now)
    _post(db, user, SocialPlatform.telegram, PostStatus.scheduled, now + timedelta(hours=1))
    db.commit()

    overview = posts_overview(db, user, since=now - timedelta(days=1))
    assert overview.total == 4
    assert overview.published == 2
    assert overview.failed == 1
    assert overview.scheduled == 1
    assert overview.success_rate == 2 / 3  # scheduled excluded, same as metrics.py


def test_posts_overview_success_rate_none_with_no_terminal_posts(db: Session, user: User) -> None:
    now = datetime.now(UTC)
    _post(db, user, SocialPlatform.telegram, PostStatus.scheduled, now)
    db.commit()
    assert posts_overview(db, user, since=now - timedelta(days=1)).success_rate is None


def test_posts_overview_excludes_posts_before_since(db: Session, user: User) -> None:
    now = datetime.now(UTC)
    _post(db, user, SocialPlatform.telegram, PostStatus.published, now - timedelta(days=10))
    db.commit()
    assert posts_overview(db, user, since=now - timedelta(days=1)).total == 0


def test_posts_overview_excludes_other_users(db: Session, user: User) -> None:
    now = datetime.now(UTC)
    other = User(email="other@cindra.dev", hashed_password="x")
    db.add(other)
    db.flush()
    db.add(Subscription(user_id=other.id))
    db.flush()
    _post(db, other, SocialPlatform.telegram, PostStatus.published, now, published_at=now)
    db.commit()

    assert posts_overview(db, user, since=now - timedelta(days=1)).total == 0


def test_posts_by_platform_breaks_down_published_and_failed(db: Session, user: User) -> None:
    now = datetime.now(UTC)
    _post(db, user, SocialPlatform.telegram, PostStatus.published, now, published_at=now)
    _post(db, user, SocialPlatform.telegram, PostStatus.failed, now)
    _post(db, user, SocialPlatform.instagram, PostStatus.published, now, published_at=now)
    db.commit()

    rows = {row.platform: row for row in posts_by_platform(db, user, since=now - timedelta(days=1))}
    assert rows[SocialPlatform.telegram].published == 1
    assert rows[SocialPlatform.telegram].failed == 1
    assert rows[SocialPlatform.instagram].published == 1
    assert rows[SocialPlatform.instagram].failed == 0


def test_posts_by_content_kind(db: Session, user: User) -> None:
    now = datetime.now(UTC)
    _post(db, user, SocialPlatform.telegram, PostStatus.published, now, content_kind="post")
    _post(db, user, SocialPlatform.telegram, PostStatus.published, now, content_kind="post")
    _post(db, user, SocialPlatform.telegram, PostStatus.published, now, content_kind="story")
    db.commit()

    counts = {row.content_kind: row.count for row in posts_by_content_kind(db, user, since=now - timedelta(days=1))}
    assert counts == {"post": 2, "story": 1}


def test_daily_published_posts_zero_fills_gaps(db: Session, user: User) -> None:
    since = datetime.now(UTC) - timedelta(days=2)
    day0 = since
    day2 = since + timedelta(days=2)
    _post(db, user, SocialPlatform.telegram, PostStatus.published, day0, published_at=day0)
    _post(db, user, SocialPlatform.telegram, PostStatus.published, day2, published_at=day2)
    db.commit()

    rows = daily_published_posts(db, user, since=since)
    by_day = {row.day: row.published for row in rows}
    assert by_day[day0.date()] == 1
    assert by_day[day2.date()] == 1
    # The day in between exists in the result with a real zero, not
    # missing entirely -- that's the whole point of zero-filling.
    middle_day = (day0 + timedelta(days=1)).date()
    assert middle_day in by_day
    assert by_day[middle_day] == 0


def test_generations_overview_and_success_rate(db: Session, user: User) -> None:
    now = datetime.now(UTC)
    _job(db, user, GenerationContentType.text, GenerationStatus.completed, now)
    _job(db, user, GenerationContentType.text, GenerationStatus.completed, now)
    _job(db, user, GenerationContentType.image, GenerationStatus.failed, now)
    _job(db, user, GenerationContentType.video, GenerationStatus.queued, now)
    db.commit()

    overview = generations_overview(db, user, since=now - timedelta(days=1))
    assert overview.total == 4
    assert overview.completed == 2
    assert overview.failed == 1
    assert overview.queued == 1
    assert overview.success_rate == 2 / 3  # queued excluded (still in flight)


def test_generations_by_content_type(db: Session, user: User) -> None:
    now = datetime.now(UTC)
    _job(db, user, GenerationContentType.text, GenerationStatus.completed, now)
    _job(db, user, GenerationContentType.image, GenerationStatus.failed, now)
    db.commit()

    rows = {
        row.content_type: row for row in generations_by_content_type(db, user, since=now - timedelta(days=1))
    }
    assert rows[GenerationContentType.text].completed == 1
    assert rows[GenerationContentType.image].failed == 1


# ---- HTTP endpoint tests ----


def test_analytics_summary_requires_auth(client: TestClient) -> None:
    assert client.get("/analytics/summary").status_code == 401


def test_analytics_summary_empty_state(client: TestClient) -> None:
    headers = _auth_headers(client)
    response = client.get("/analytics/summary", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["posts_total"] == 0
    assert body["publish_success_rate"] is None
    assert body["generations_total"] == 0
    assert body["generation_success_rate"] is None
    assert body["posts_by_platform"] == []
    assert body["posts_by_content_kind"] == []
    # 30 days inclusive of today -> 31 zero-filled rows.
    assert len(body["daily_published_posts"]) == 31
    assert all(row["published"] == 0 for row in body["daily_published_posts"])


def test_analytics_summary_reflects_usage_against_free_tier_limits(
    client: TestClient, db: Session
) -> None:
    headers = _auth_headers(client, "usage@cindra.dev")
    user = db.scalar(select(User).where(User.email == "usage@cindra.dev"))
    db.add(UsageEvent(user_id=user.id, event_type=UsageEventType.generation, content_type=GenerationContentType.text))
    db.add(UsageEvent(user_id=user.id, event_type=UsageEventType.generation, content_type=GenerationContentType.text))
    db.add(UsageEvent(user_id=user.id, event_type=UsageEventType.publication))
    db.commit()

    body = client.get("/analytics/summary", headers=headers).json()
    usage_by_kind = {(row["event_type"], row["content_type"]): row for row in body["usage_this_period"]}

    text_row = usage_by_kind[("generation", "text")]
    assert text_row["used"] == 2
    assert text_row["limit"] == 20  # free tier, app/plans.py

    publication_row = usage_by_kind[("publication", None)]
    assert publication_row["used"] == 1
    assert publication_row["limit"] == 10  # free tier

    image_row = usage_by_kind[("generation", "image")]
    assert image_row["used"] == 0
    assert image_row["limit"] == 3


def test_analytics_summary_isolates_users(client: TestClient, db: Session) -> None:
    other_headers = _auth_headers(client, "owner-of-data@cindra.dev")
    other_user = db.scalar(select(User).where(User.email == "owner-of-data@cindra.dev"))
    _post(db, other_user, SocialPlatform.telegram, PostStatus.published, datetime.now(UTC), published_at=datetime.now(UTC))
    db.commit()

    my_headers = _auth_headers(client, "viewer@cindra.dev")
    body = client.get("/analytics/summary", headers=my_headers).json()
    assert body["posts_total"] == 0

    other_body = client.get("/analytics/summary", headers=other_headers).json()
    assert other_body["posts_total"] == 1


def test_days_query_param_is_bounded(client: TestClient) -> None:
    headers = _auth_headers(client)
    assert client.get("/analytics/summary?days=0", headers=headers).status_code == 422
    assert client.get("/analytics/summary?days=366", headers=headers).status_code == 422
    assert client.get("/analytics/summary?days=1", headers=headers).status_code == 200
