from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import (
    GenerationContentType,
    Subscription,
    SubscriptionStatus,
    SubscriptionTier,
    UsageEvent,
    UsageEventType,
    User,
)
from app.plans import effective_tier
from app.usage import enforce_and_record_usage, enforce_and_record_usage_bulk


def _usage_count(db: Session, user: User) -> int:
    return len(
        list(db.scalars(select(UsageEvent).where(UsageEvent.user_id == user.id)))
    )


def test_records_usage_event_when_under_limit(db: Session, user: User) -> None:
    enforce_and_record_usage(
        db, user, UsageEventType.generation, GenerationContentType.text
    )
    assert _usage_count(db, user) == 1


def test_raises_402_once_free_tier_image_limit_is_reached(db: Session, user: User) -> None:
    for _ in range(3):  # free tier image limit, see app/plans.py
        enforce_and_record_usage(
            db, user, UsageEventType.generation, GenerationContentType.image
        )

    with pytest.raises(HTTPException) as exc_info:
        enforce_and_record_usage(
            db, user, UsageEventType.generation, GenerationContentType.image
        )

    assert exc_info.value.status_code == 402
    assert _usage_count(db, user) == 3  # the rejected call didn't record an event


def test_free_tier_video_is_blocked_on_the_first_attempt(db: Session, user: User) -> None:
    # Free tier's video limit is 0 -- the very first attempt is already
    # over budget, unlike text/image which allow a few before blocking.
    with pytest.raises(HTTPException) as exc_info:
        enforce_and_record_usage(
            db, user, UsageEventType.generation, GenerationContentType.video
        )
    assert exc_info.value.status_code == 402
    assert _usage_count(db, user) == 0


def test_formats_are_limited_independently(db: Session, user: User) -> None:
    for _ in range(3):
        enforce_and_record_usage(
            db, user, UsageEventType.generation, GenerationContentType.image
        )

    # image is exhausted, but text has its own counter and limit
    enforce_and_record_usage(
        db, user, UsageEventType.generation, GenerationContentType.text
    )
    assert _usage_count(db, user) == 4


def test_pro_tier_still_limits_video_even_though_text_is_generous(db: Session, user: User) -> None:
    db.execute(
        update(Subscription)
        .where(Subscription.user_id == user.id)
        .values(tier=SubscriptionTier.pro)
    )
    db.commit()

    for _ in range(6):  # pro tier video limit, see app/plans.py
        enforce_and_record_usage(
            db, user, UsageEventType.generation, GenerationContentType.video
        )

    with pytest.raises(HTTPException) as exc_info:
        enforce_and_record_usage(
            db, user, UsageEventType.generation, GenerationContentType.video
        )
    assert exc_info.value.status_code == 402

    # text's soft-cap (300) is nowhere near reached by 25 calls
    for _ in range(25):
        enforce_and_record_usage(
            db, user, UsageEventType.generation, GenerationContentType.text
        )
    assert _usage_count(db, user) == 6 + 25


def test_business_tier_allows_far_more_video_than_pro(db: Session, user: User) -> None:
    db.execute(
        update(Subscription)
        .where(Subscription.user_id == user.id)
        .values(tier=SubscriptionTier.business)
    )
    db.commit()

    for _ in range(55):  # business tier video limit, see app/plans.py
        enforce_and_record_usage(
            db, user, UsageEventType.generation, GenerationContentType.video
        )
    assert _usage_count(db, user) == 55

    with pytest.raises(HTTPException):
        enforce_and_record_usage(
            db, user, UsageEventType.generation, GenerationContentType.video
        )


def test_effective_tier_is_free_for_a_cancelled_business_subscription() -> None:
    # CIN-153: subscription.tier only ever moves forward (set once by
    # confirm_paypal_subscription, never reset) -- effective_tier is
    # what everything enforcing a limit must use instead.
    subscription = Subscription(
        user_id=None, tier=SubscriptionTier.business, status=SubscriptionStatus.canceled
    )
    assert effective_tier(subscription) is SubscriptionTier.free


def test_effective_tier_is_free_for_a_past_due_subscription() -> None:
    subscription = Subscription(
        user_id=None, tier=SubscriptionTier.pro, status=SubscriptionStatus.past_due
    )
    assert effective_tier(subscription) is SubscriptionTier.free


def test_effective_tier_is_the_real_tier_when_active() -> None:
    subscription = Subscription(
        user_id=None, tier=SubscriptionTier.business, status=SubscriptionStatus.active
    )
    assert effective_tier(subscription) is SubscriptionTier.business


def test_cancelled_business_subscription_gets_free_tier_quota(
    db: Session, user: User
) -> None:
    # The concrete exploit CIN-153 closes: subscribe to Business once,
    # cancel, and previously keep Business-level quota forever.
    db.execute(
        update(Subscription)
        .where(Subscription.user_id == user.id)
        .values(tier=SubscriptionTier.business, status=SubscriptionStatus.canceled)
    )
    db.commit()

    for _ in range(3):  # free tier image limit, see app/plans.py
        enforce_and_record_usage(
            db, user, UsageEventType.generation, GenerationContentType.image
        )
    with pytest.raises(HTTPException) as exc_info:
        enforce_and_record_usage(
            db, user, UsageEventType.generation, GenerationContentType.image
        )
    assert exc_info.value.status_code == 402
    # The 402 message must not claim a tier the user no longer has.
    assert "business" not in exc_info.value.detail
    assert "free" in exc_info.value.detail


def test_past_due_pro_subscription_gets_free_tier_quota(db: Session, user: User) -> None:
    db.execute(
        update(Subscription)
        .where(Subscription.user_id == user.id)
        .values(tier=SubscriptionTier.pro, status=SubscriptionStatus.past_due)
    )
    db.commit()

    # free tier has zero video generations -- pro would allow 6
    with pytest.raises(HTTPException) as exc_info:
        enforce_and_record_usage(
            db, user, UsageEventType.generation, GenerationContentType.video
        )
    assert exc_info.value.status_code == 402


def test_active_business_subscription_is_unaffected(db: Session, user: User) -> None:
    db.execute(
        update(Subscription)
        .where(Subscription.user_id == user.id)
        .values(tier=SubscriptionTier.business, status=SubscriptionStatus.active)
    )
    db.commit()

    for _ in range(55):  # business tier video limit, unaffected by this fix
        enforce_and_record_usage(
            db, user, UsageEventType.generation, GenerationContentType.video
        )
    assert _usage_count(db, user) == 55


def test_only_current_period_counts_toward_the_limit(db: Session, user: User) -> None:
    last_month = datetime.now(UTC).replace(day=1) - timedelta(days=1)
    for _ in range(3):
        db.add(
            UsageEvent(
                user_id=user.id,
                event_type=UsageEventType.generation,
                content_type=GenerationContentType.image,
                created_at=last_month,
            )
        )
    db.commit()

    # All 3 prior events are outside this billing period, so this call
    # is still the 1st of the new period, not the 4th overall.
    enforce_and_record_usage(
        db, user, UsageEventType.generation, GenerationContentType.image
    )
    assert _usage_count(db, user) == 4


def test_publication_and_generation_limits_are_independent(db: Session, user: User) -> None:
    for _ in range(3):
        enforce_and_record_usage(
            db, user, UsageEventType.generation, GenerationContentType.image
        )

    # image generation is exhausted, but publication has its own counter
    enforce_and_record_usage(db, user, UsageEventType.publication)
    assert _usage_count(db, user) == 4


def test_bulk_records_count_events_at_once(db: Session, user: User) -> None:
    enforce_and_record_usage_bulk(db, user, UsageEventType.publication, count=3)
    assert _usage_count(db, user) == 3


def test_bulk_raises_402_when_the_whole_batch_would_exceed_the_limit(
    db: Session, user: User
) -> None:
    # Free tier's publication limit is 10/month (see app/plans.py).
    enforce_and_record_usage_bulk(db, user, UsageEventType.publication, count=9)

    with pytest.raises(HTTPException) as exc_info:
        enforce_and_record_usage_bulk(db, user, UsageEventType.publication, count=2)
    assert exc_info.value.status_code == 402
    # Nothing from the rejected batch of 2 got recorded -- all or nothing.
    assert _usage_count(db, user) == 9


def test_bulk_exactly_at_the_limit_succeeds(db: Session, user: User) -> None:
    enforce_and_record_usage_bulk(db, user, UsageEventType.publication, count=10)
    assert _usage_count(db, user) == 10
