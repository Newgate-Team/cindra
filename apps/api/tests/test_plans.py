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
