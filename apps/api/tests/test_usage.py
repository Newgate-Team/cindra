from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Subscription, SubscriptionTier, UsageEvent, UsageEventType, User
from app.usage import enforce_and_record_usage


def _usage_count(db: Session, user: User) -> int:
    return len(
        list(db.scalars(select(UsageEvent).where(UsageEvent.user_id == user.id)))
    )


def test_records_usage_event_when_under_limit(db: Session, user: User) -> None:
    enforce_and_record_usage(db, user, UsageEventType.generation)
    assert _usage_count(db, user) == 1


def test_raises_402_once_free_tier_limit_is_reached(db: Session, user: User) -> None:
    for _ in range(10):  # free tier limit, see app/plans.py
        enforce_and_record_usage(db, user, UsageEventType.generation)

    with pytest.raises(HTTPException) as exc_info:
        enforce_and_record_usage(db, user, UsageEventType.generation)

    assert exc_info.value.status_code == 402
    assert _usage_count(db, user) == 10  # the rejected call didn't record an event


def test_pro_tier_is_never_limited(db: Session, user: User) -> None:
    db.execute(
        update(Subscription)
        .where(Subscription.user_id == user.id)
        .values(tier=SubscriptionTier.pro)
    )
    db.commit()

    for _ in range(25):
        enforce_and_record_usage(db, user, UsageEventType.generation)

    assert _usage_count(db, user) == 25


def test_only_current_period_counts_toward_the_limit(db: Session, user: User) -> None:
    last_month = datetime.now(UTC).replace(day=1) - timedelta(days=1)
    for _ in range(10):
        db.add(
            UsageEvent(
                user_id=user.id, event_type=UsageEventType.generation, created_at=last_month
            )
        )
    db.commit()

    # All 10 prior events are outside this billing period, so this
    # call is still the 1st of the new period, not the 11th overall.
    enforce_and_record_usage(db, user, UsageEventType.generation)
    assert _usage_count(db, user) == 11


def test_publication_and_generation_limits_are_independent(db: Session, user: User) -> None:
    for _ in range(10):
        enforce_and_record_usage(db, user, UsageEventType.generation)

    # generation is exhausted, but publication has its own counter
    enforce_and_record_usage(db, user, UsageEventType.publication)
    assert _usage_count(db, user) == 11
