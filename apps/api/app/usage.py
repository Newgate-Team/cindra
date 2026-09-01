from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    GenerationContentType,
    Subscription,
    UsageEvent,
    UsageEventType,
    User,
)
from app.plans import limit_for


def _kind_label(
    event_type: UsageEventType, content_type: GenerationContentType | None
) -> str:
    # This ends up in a 402 shown to the user, so the long-clip counter
    # (CIN-146) needs a phrase rather than its raw enum value.
    if event_type is UsageEventType.long_video_generation:
        return "длинных AI-роликов"
    if content_type is None:
        return event_type.value
    return f"{content_type.value} {event_type.value}"


def _current_period_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _check_limit(
    db: Session, user: User, event_type: UsageEventType, content_type: GenerationContentType | None, count: int
) -> None:
    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    limit = limit_for(subscription.tier, event_type, content_type)

    if limit is not None:
        period_start = _current_period_start()
        query = (
            select(func.count())
            .select_from(UsageEvent)
            .where(
                UsageEvent.user_id == user.id,
                UsageEvent.event_type == event_type,
                UsageEvent.created_at >= period_start,
            )
        )
        if content_type is not None:
            query = query.where(UsageEvent.content_type == content_type)
        current_usage = db.scalar(query)
        if current_usage + count > limit:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Лимит тарифа исчерпан: {limit} {_kind_label(event_type, content_type)} "
                    f"в месяц на тарифе {subscription.tier.value}"
                ),
            )


def check_usage_limit(
    db: Session,
    user: User,
    event_type: UsageEventType,
    content_type: GenerationContentType | None = None,
    count: int = 1,
) -> None:
    """Raise 402 if `count` more events wouldn't fit the tier limit,
    without recording anything (CIN-139).

    Split out of enforce_and_record_usage for the synchronous studio
    endpoints: those know within the same request whether the
    generation actually succeeded, so they check first, call the model,
    and only then record -- a Gemini outage must not burn the user's
    monthly quota. The async job endpoints keep charging up front,
    since there the result only arrives in a worker long after the
    response.
    """
    _check_limit(db, user, event_type, content_type, count=count)


def record_usage(
    db: Session,
    user: User,
    event_type: UsageEventType,
    content_type: GenerationContentType | None = None,
    count: int = 1,
) -> None:
    """Record `count` usage events without re-checking the limit --
    pairs with check_usage_limit around a fallible operation."""
    db.add_all(
        [
            UsageEvent(user_id=user.id, event_type=event_type, content_type=content_type)
            for _ in range(count)
        ]
    )
    db.commit()


def enforce_and_record_usage(
    db: Session,
    user: User,
    event_type: UsageEventType,
    content_type: GenerationContentType | None = None,
) -> None:
    """Raise 402 if `user` is over their tier's limit for `event_type`
    (and, for generations, `content_type` -- text/image/video are
    limited independently, see app/plans.py) this billing period,
    otherwise record the event.

    "This billing period" is the calendar month -- Subscription
    doesn't track a real period_start yet (no payment provider, see
    gate ticket CIN-18/CIN-20), so there's nothing more precise to
    anchor it to. Tier limit numbers are fixed in CIN-59; this only
    owns the enforcement mechanism.
    """
    _check_limit(db, user, event_type, content_type, count=1)
    db.add(UsageEvent(user_id=user.id, event_type=event_type, content_type=content_type))
    db.commit()


def enforce_and_record_usage_bulk(
    db: Session,
    user: User,
    event_type: UsageEventType,
    count: int,
    content_type: GenerationContentType | None = None,
) -> None:
    """Same as `enforce_and_record_usage`, but for a fan-out publish
    (CIN-106) where one request creates `count` events at once (one
    per target account). The whole batch is checked as a single unit
    against the remaining limit -- either all `count` events fit, or
    none of them are recorded, rather than silently publishing some
    prefix of the requested targets and dropping the rest.
    """
    _check_limit(db, user, event_type, content_type, count=count)
    db.add_all(
        [UsageEvent(user_id=user.id, event_type=event_type, content_type=content_type) for _ in range(count)]
    )
    db.commit()
