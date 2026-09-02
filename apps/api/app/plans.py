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
    # CIN-146: long AI clips (Seedance, CIN-144) are billed separately
    # from the 8-second Veo clips counted in max_generations_per_format
    # -- at ~$7 apiece they'd blow a tier's whole margin in a handful
    # of generations.
    max_long_videos_per_month: int | None = 0
    # CIN-148: laid-out renders are free to produce (no model call), so
    # this bounds storage abuse rather than cost -- generous on purpose.
    max_layout_renders_per_month: int | None = 30


# Values fixed in CIN-59, derived from real per-generation AI cost
# (Gemini 2.5 Flash-Lite / Imagen 4 / Veo 3.1 Fast) at a target margin
# per tier -- see the unit-economics report linked from that ticket.
# `None` means unlimited; text limits are a soft-cap against abuse
# rather than a real cost constraint (text is ~$0.0001/generation).
#
# CIN-146 re-checked these against official pricing on 2026-09-01 and
# left the per-format numbers as they are: on Veo 3.1 Fast ($0.12/s,
# so $0.96 per 8s clip) Pro costs ~$8.7 against $19 and Business ~$60
# against $100. The long-video counter is new: a 15s Seedance 2.5 clip
# at 720p runs ~$7.10 ($0.473/s), so Business gets 3 (~$21 on top of
# ~$60) and the cheaper tiers none -- one 30s clip per user would cost
# more than a whole Pro subscription.
PLAN_LIMITS: dict[SubscriptionTier, PlanLimits] = {
    SubscriptionTier.free: PlanLimits(
        max_generations_per_format={
            GenerationContentType.text: 20,
            GenerationContentType.image: 3,
            GenerationContentType.video: 0,
        },
        max_publications_per_month=10,
        max_connected_accounts=1,
        max_layout_renders_per_month=30,
    ),
    SubscriptionTier.pro: PlanLimits(
        max_generations_per_format={
            GenerationContentType.text: 300,
            GenerationContentType.image: 60,
            GenerationContentType.video: 6,
        },
        max_publications_per_month=None,
        max_connected_accounts=None,
        max_long_videos_per_month=0,
        max_layout_renders_per_month=500,
    ),
    SubscriptionTier.business: PlanLimits(
        max_generations_per_format={
            GenerationContentType.text: 600,
            GenerationContentType.image: 150,
            GenerationContentType.video: 55,
        },
        max_publications_per_month=None,
        max_connected_accounts=None,
        max_long_videos_per_month=3,
        max_layout_renders_per_month=None,
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
    if event_type is UsageEventType.long_video_generation:
        return limits.max_long_videos_per_month
    if event_type is UsageEventType.layout_render:
        return limits.max_layout_renders_per_month
    return limits.max_publications_per_month
