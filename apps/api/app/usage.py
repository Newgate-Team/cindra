from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Subscription, UsageEvent, UsageEventType, User
from app.plans import limit_for


def _current_period_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def enforce_and_record_usage(db: Session, user: User, event_type: UsageEventType) -> None:
    """Raise 402 if `user` is over their tier's limit for `event_type`
    this billing period, otherwise record the event.

    "This billing period" is the calendar month -- Subscription
    doesn't track a real period_start yet (no payment provider, see
    gate ticket CIN-18/CIN-20), so there's nothing more precise to
    anchor it to. Free-tier limit numbers are themselves placeholders
    (see app/plans.py); this only owns the mechanism.
    """
    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    limit = limit_for(subscription.tier, event_type)

    if limit is not None:
        period_start = _current_period_start()
        current_usage = db.scalar(
            select(func.count())
            .select_from(UsageEvent)
            .where(
                UsageEvent.user_id == user.id,
                UsageEvent.event_type == event_type,
                UsageEvent.created_at >= period_start,
            )
        )
        if current_usage >= limit:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Лимит тарифа исчерпан: {limit} {event_type.value} в месяц "
                    f"на тарифе {subscription.tier.value}"
                ),
            )

    db.add(UsageEvent(user_id=user.id, event_type=event_type))
    db.commit()
