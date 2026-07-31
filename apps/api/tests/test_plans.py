from app.models import SubscriptionTier, UsageEventType
from app.plans import limit_for


def test_free_tier_has_finite_limits() -> None:
    assert limit_for(SubscriptionTier.free, UsageEventType.generation) == 10
    assert limit_for(SubscriptionTier.free, UsageEventType.publication) == 10


def test_pro_tier_is_unlimited() -> None:
    assert limit_for(SubscriptionTier.pro, UsageEventType.generation) is None
    assert limit_for(SubscriptionTier.pro, UsageEventType.publication) is None
