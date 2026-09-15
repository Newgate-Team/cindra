from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    GenerationContentType,
    GenerationJob,
    GenerationStatus,
    Post,
    PostStatus,
    SocialAccount,
    SocialPlatform,
    User,
)


@dataclass(frozen=True)
class PostsOverview:
    total: int
    scheduled: int
    publishing: int
    published: int
    failed: int

    @property
    def success_rate(self) -> float | None:
        # Same "terminal posts only" definition as app/metrics.py's
        # publish_success_rate -- a still-scheduled post hasn't
        # succeeded or failed yet, so it shouldn't drag the rate down.
        terminal = self.published + self.failed
        return self.published / terminal if terminal else None


def posts_overview(db: Session, user: User, since: datetime) -> PostsOverview:
    rows = db.execute(
        select(Post.status, func.count())
        .where(Post.user_id == user.id, Post.created_at >= since)
        .group_by(Post.status)
    ).all()
    counts = dict(rows)
    return PostsOverview(
        total=sum(counts.values()),
        scheduled=counts.get(PostStatus.scheduled, 0),
        publishing=counts.get(PostStatus.publishing, 0),
        published=counts.get(PostStatus.published, 0),
        failed=counts.get(PostStatus.failed, 0),
    )


@dataclass(frozen=True)
class PlatformPostCounts:
    platform: SocialPlatform
    published: int
    failed: int


def posts_by_platform(db: Session, user: User, since: datetime) -> list[PlatformPostCounts]:
    rows = db.execute(
        select(SocialAccount.platform, Post.status, func.count())
        .join(SocialAccount, SocialAccount.id == Post.social_account_id)
        .where(Post.user_id == user.id, Post.created_at >= since)
        .group_by(SocialAccount.platform, Post.status)
    ).all()
    by_platform: dict[SocialPlatform, dict[PostStatus, int]] = {}
    for platform, status, count in rows:
        by_platform.setdefault(platform, {})[status] = count
    return [
        PlatformPostCounts(
            platform=platform,
            published=statuses.get(PostStatus.published, 0),
            failed=statuses.get(PostStatus.failed, 0),
        )
        for platform, statuses in sorted(by_platform.items(), key=lambda item: item[0].value)
    ]


@dataclass(frozen=True)
class ContentKindCount:
    content_kind: str
    count: int


def posts_by_content_kind(db: Session, user: User, since: datetime) -> list[ContentKindCount]:
    rows = db.execute(
        select(Post.content_kind, func.count())
        .where(Post.user_id == user.id, Post.created_at >= since)
        .group_by(Post.content_kind)
        .order_by(func.count().desc())
    ).all()
    return [ContentKindCount(content_kind=kind, count=count) for kind, count in rows]


@dataclass(frozen=True)
class DailyPostCount:
    day: date
    published: int


def daily_published_posts(db: Session, user: User, since: datetime) -> list[DailyPostCount]:
    """One row per calendar day from `since` to today (UTC), zero-filled
    -- a chart over a gap-free range reads far more clearly than one
    that silently skips days with nothing published.

    Bucketed in Python, not via a DB-side DATE()/date_trunc -- that
    would truncate using the Postgres session's own timezone, which is
    whatever the server happens to be configured with (seen locally as
    +05, not UTC) and isn't something application code controls or
    should depend on. Every other timestamp in this codebase is
    UTC-anchored (datetime.now(UTC) throughout) -- bucketing here does
    the same.
    """
    published_ats = db.scalars(
        select(Post.published_at).where(
            Post.user_id == user.id,
            Post.status == PostStatus.published,
            Post.published_at >= since,
        )
    ).all()
    counts_by_day: dict[date, int] = {}
    for published_at in published_ats:
        day = published_at.astimezone(UTC).date()
        counts_by_day[day] = counts_by_day.get(day, 0) + 1

    today = datetime.now(UTC).date()
    first_day = since.date()
    days = []
    current = first_day
    while current <= today:
        days.append(DailyPostCount(day=current, published=counts_by_day.get(current, 0)))
        current += timedelta(days=1)
    return days


@dataclass(frozen=True)
class GenerationsOverview:
    total: int
    queued: int
    processing: int
    completed: int
    failed: int
    flagged: int

    @property
    def success_rate(self) -> float | None:
        # Terminal here excludes queued/processing (still in flight);
        # flagged (moderation-blocked, see content_pipeline) counts as
        # a non-success outcome alongside failed.
        terminal = self.completed + self.failed + self.flagged
        return self.completed / terminal if terminal else None


def generations_overview(db: Session, user: User, since: datetime) -> GenerationsOverview:
    rows = db.execute(
        select(GenerationJob.status, func.count())
        .where(GenerationJob.user_id == user.id, GenerationJob.created_at >= since)
        .group_by(GenerationJob.status)
    ).all()
    counts = dict(rows)
    return GenerationsOverview(
        total=sum(counts.values()),
        queued=counts.get(GenerationStatus.queued, 0),
        processing=counts.get(GenerationStatus.processing, 0),
        completed=counts.get(GenerationStatus.completed, 0),
        failed=counts.get(GenerationStatus.failed, 0),
        flagged=counts.get(GenerationStatus.flagged, 0),
    )


@dataclass(frozen=True)
class ContentTypeGenerationCounts:
    content_type: GenerationContentType
    completed: int
    failed: int


def generations_by_content_type(
    db: Session, user: User, since: datetime
) -> list[ContentTypeGenerationCounts]:
    rows = db.execute(
        select(GenerationJob.content_type, GenerationJob.status, func.count())
        .where(GenerationJob.user_id == user.id, GenerationJob.created_at >= since)
        .group_by(GenerationJob.content_type, GenerationJob.status)
    ).all()
    by_type: dict[GenerationContentType, dict[GenerationStatus, int]] = {}
    for content_type, status, count in rows:
        by_type.setdefault(content_type, {})[status] = count
    return [
        ContentTypeGenerationCounts(
            content_type=content_type,
            completed=statuses.get(GenerationStatus.completed, 0),
            failed=statuses.get(GenerationStatus.failed, 0),
        )
        for content_type, statuses in sorted(by_type.items(), key=lambda item: item[0].value)
    ]
