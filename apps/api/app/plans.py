from dataclasses import dataclass

from app.models import SubscriptionTier, UsageEventType


@dataclass(frozen=True)
class PlanLimits:
    max_generations_per_month: int | None
    max_publications_per_month: int | None
    max_connected_accounts: int | None


# Placeholder values -- actual tier boundaries are an open product
# decision (see docs/spec.md §9 and README.md §1: "Границы тарифов
# подписки"), not yet fixed. `None` means unlimited. Update once
# that's decided; the enforcement mechanism itself (app/usage.py)
# doesn't change.
PLAN_LIMITS: dict[SubscriptionTier, PlanLimits] = {
    SubscriptionTier.free: PlanLimits(
        max_generations_per_month=10,
        max_publications_per_month=10,
        max_connected_accounts=1,
    ),
    SubscriptionTier.pro: PlanLimits(
        max_generations_per_month=None,
        max_publications_per_month=None,
        max_connected_accounts=None,
    ),
}

_EVENT_TYPE_TO_LIMIT_FIELD = {
    UsageEventType.generation: "max_generations_per_month",
    UsageEventType.publication: "max_publications_per_month",
}


def limit_for(tier: SubscriptionTier, event_type: UsageEventType) -> int | None:
    field_name = _EVENT_TYPE_TO_LIMIT_FIELD[event_type]
    return getattr(PLAN_LIMITS[tier], field_name)
