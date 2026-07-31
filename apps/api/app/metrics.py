from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Post, PostStatus, User


def time_to_first_post_seconds(db: Session, user: User) -> float | None:
    """Seconds between registration and this user's first *published* post,
    or None if they haven't published one yet."""
    first_published_at = db.scalar(
        select(func.min(Post.published_at)).where(
            Post.user_id == user.id, Post.status == PostStatus.published
        )
    )
    if first_published_at is None:
        return None
    return (first_published_at - user.created_at).total_seconds()


def average_time_to_first_post_seconds(db: Session) -> float | None:
    """Average time-to-first-post across every user who has published at
    least one post. None if nobody has yet."""
    first_published_by_user = (
        select(Post.user_id, func.min(Post.published_at).label("first_published_at"))
        .where(Post.status == PostStatus.published)
        .group_by(Post.user_id)
        .subquery()
    )
    avg_seconds = db.scalar(
        select(
            func.avg(
                func.extract("epoch", first_published_by_user.c.first_published_at)
                - func.extract("epoch", User.created_at)
            )
        ).select_from(first_published_by_user.join(User, User.id == first_published_by_user.c.user_id))
    )
    return float(avg_seconds) if avg_seconds is not None else None


def retention(db: Session, days: int) -> float:
    """Fraction (0-1) of users registered at least `days` ago who published
    at least one post on or after day `days` since registering.

    Cohort excludes users who registered less recently than `days` ago --
    someone who signed up yesterday hasn't had the chance to be a "day 30
    retained" user yet, counting them as churned would understate retention.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    cohort = select(User).where(User.created_at <= cutoff).subquery()
    cohort_size = db.scalar(select(func.count()).select_from(cohort))
    if not cohort_size:
        return 0.0

    retained = db.scalar(
        select(func.count(func.distinct(Post.user_id)))
        .select_from(Post)
        .join(cohort, cohort.c.id == Post.user_id)
        .where(
            Post.status == PostStatus.published,
            Post.published_at >= cohort.c.created_at + timedelta(days=days),
        )
    )
    return retained / cohort_size


def publish_success_rate(db: Session, since: datetime | None = None) -> float | None:
    """Fraction (0-1) of *terminal* posts (published or failed -- excludes
    still-scheduled/publishing ones, which haven't succeeded or failed yet)
    that ended up published. None if there are no terminal posts."""
    query = select(Post.status).where(
        Post.status.in_([PostStatus.published, PostStatus.failed])
    )
    if since is not None:
        query = query.where(Post.created_at >= since)

    statuses = list(db.scalars(query))
    if not statuses:
        return None
    return sum(1 for s in statuses if s == PostStatus.published) / len(statuses)
