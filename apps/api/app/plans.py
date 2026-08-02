from dataclasses import dataclass

from app.models import GenerationContentType, SubscriptionTier, UsageEventType


@dataclass(frozen=True)
class PlanLimits:
    # Per-format, not a single total -- text/image/video costs are
    # four orders of magnitude apart (see CIN-59), so a single
    # "N generations" number is either too generous for video or too
    # tight for text no matter where it's set.
    max_generations_per_format: dict[GenerationContentType, int | None]
    max_publications_per_month: int | None
    max_connected_accounts: int | None


# Values fixed in CIN-59, derived from real per-generation AI cost
# (Gemini 2.5 Flash-Lite / Imagen 4 / Veo 3.1 Fast) at a target margin
# per tier -- see the unit-economics report linked from that ticket.
# `None` means unlimited; text limits are a soft-cap against abuse
# rather than a real cost constraint (text is ~$0.0001/generation).
PLAN_LIMITS: dict[SubscriptionTier, PlanLimits] = {
    SubscriptionTier.free: PlanLimits(
        max_generations_per_format={
            GenerationContentType.text: 20,
            GenerationContentType.image: 3,
            GenerationContentType.video: 0,
        },
        max_publications_per_month=10,
        max_connected_accounts=1,
    ),
    SubscriptionTier.pro: PlanLimits(
        max_generations_per_format={
            GenerationContentType.text: 300,
            GenerationContentType.image: 60,
            GenerationContentType.video: 6,
        },
        max_publications_per_month=None,
        max_connected_accounts=None,
    ),
    SubscriptionTier.business: PlanLimits(
        max_generations_per_format={
            GenerationContentType.text: 600,
            GenerationContentType.image: 150,
            GenerationContentType.video: 55,
        },
        max_publications_per_month=None,
        max_connected_accounts=None,
    ),
}


def limit_for(
    tier: SubscriptionTier,
    event_type: UsageEventType,
    content_type: GenerationContentType | None = None,
) -> int | None:
    """`content_type` is required for `UsageEventType.generation` (each
    format has its own limit) and ignored for `.publication` (one
    limit covers all platforms -- publishing cost doesn't vary by
    format the way generation does).
    """
    limits = PLAN_LIMITS[tier]
    if event_type is UsageEventType.generation:
        if content_type is None:
            raise ValueError("content_type is required to look up a generation limit")
        return limits.max_generations_per_format[content_type]
    return limits.max_publications_per_month
