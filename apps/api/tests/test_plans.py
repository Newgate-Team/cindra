import pytest

from app.models import GenerationContentType, SubscriptionTier, UsageEventType
from app.plans import limit_for


def test_free_tier_has_finite_per_format_limits() -> None:
    assert limit_for(SubscriptionTier.free, UsageEventType.generation, GenerationContentType.text) == 20
    assert limit_for(SubscriptionTier.free, UsageEventType.generation, GenerationContentType.image) == 3
    assert limit_for(SubscriptionTier.free, UsageEventType.generation, GenerationContentType.video) == 0
    assert limit_for(SubscriptionTier.free, UsageEventType.publication) == 10


def test_pro_tier_has_generous_but_finite_per_format_limits() -> None:
    assert limit_for(SubscriptionTier.pro, UsageEventType.generation, GenerationContentType.text) == 300
    assert limit_for(SubscriptionTier.pro, UsageEventType.generation, GenerationContentType.image) == 60
    assert limit_for(SubscriptionTier.pro, UsageEventType.generation, GenerationContentType.video) == 6
    assert limit_for(SubscriptionTier.pro, UsageEventType.publication) is None


def test_business_tier_gives_far_more_video() -> None:
    assert limit_for(SubscriptionTier.business, UsageEventType.generation, GenerationContentType.text) == 600
    assert limit_for(SubscriptionTier.business, UsageEventType.generation, GenerationContentType.image) == 150
    assert limit_for(SubscriptionTier.business, UsageEventType.generation, GenerationContentType.video) == 55
    assert limit_for(SubscriptionTier.business, UsageEventType.publication) is None


def test_generation_limit_requires_content_type() -> None:
    with pytest.raises(ValueError):
        limit_for(SubscriptionTier.free, UsageEventType.generation)


def test_long_video_limit_is_business_only() -> None:
    # CIN-146: a 15s Seedance clip costs ~$7.10 -- more than a whole
    # Pro subscription, so only Business gets any, and only a few.
    assert limit_for(SubscriptionTier.free, UsageEventType.long_video_generation) == 0
    assert limit_for(SubscriptionTier.pro, UsageEventType.long_video_generation) == 0
    assert limit_for(SubscriptionTier.business, UsageEventType.long_video_generation) == 3


def test_long_video_limit_needs_no_content_type() -> None:
    # It's an event type, not a content type -- the job itself is an
    # ordinary video everywhere except billing.
    assert limit_for(SubscriptionTier.business, UsageEventType.long_video_generation) == 3
