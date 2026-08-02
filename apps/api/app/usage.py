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


def _current_period_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


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
        if current_usage >= limit:
            kind = event_type.value if content_type is None else f"{content_type.value} {event_type.value}"
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Лимит тарифа исчерпан: {limit} {kind} в месяц "
                    f"на тарифе {subscription.tier.value}"
                ),
            )

    db.add(UsageEvent(user_id=user.id, event_type=event_type, content_type=content_type))
    db.commit()
