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
from app.plans import effective_tier, limit_for


def _kind_label(
    event_type: UsageEventType, content_type: GenerationContentType | None
) -> str:
    # This ends up in a 402 shown to the user, so the long-clip counter
    # (CIN-146) needs a phrase rather than its raw enum value.
    if event_type is UsageEventType.long_video_generation:
        return "длинных AI-роликов"
    if event_type is UsageEventType.layout_render:
        return "карточек по шаблону"
    if content_type is None:
        return event_type.value
    return f"{content_type.value} {event_type.value}"


def _current_period_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _check_limit(
    db: Session,
    user: User,
    event_type: UsageEventType,
    content_type: GenerationContentType | None,
    count: int,
    for_update: bool = False,
) -> None:
    # CIN-158: `for_update` closes a TOCTOU race -- two concurrent
    # requests can both read the same (pre-insert) usage count and both
    # pass the check, over-provisioning past the limit. Locking the
    # user's Subscription row (one per user, so it's a natural mutex)
    # serializes the read-count-then-insert sequence for that user.
    #
    # Only safe for callers that check and record in the SAME short
    # transaction (enforce_and_record_usage/_bulk below) -- holding this
    # lock across a slow external call (Gemini/Veo/Seedance) would
    # serialize a user's unrelated concurrent requests for the whole
    # duration of that call, and risks tying up the connection pool.
    # check_usage_limit (CIN-139's check-now/record-after-the-model-call
    # split) deliberately passes for_update=False and keeps the
    # narrower race it already had -- see that function's docstring.
    query = select(Subscription).where(Subscription.user_id == user.id)
    if for_update:
        query = query.with_for_update()
    subscription = db.scalar(query)
    # CIN-153: never subscription.tier directly -- a cancelled or
    # payment-failed subscription must not keep premium quota.
    tier = effective_tier(subscription)
    limit = limit_for(tier, event_type, content_type)

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
        if current_usage + count > limit:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Лимит тарифа исчерпан: {limit} {_kind_label(event_type, content_type)} "
                    f"в месяц на тарифе {tier.value}"
                ),
            )


def check_usage_limit(
    db: Session,
    user: User,
    event_type: UsageEventType,
    content_type: GenerationContentType | None = None,
    count: int = 1,
    for_update: bool = False,
) -> None:
    """Raise 402 if `count` more events wouldn't fit the tier limit,
    without recording anything (CIN-139).

    Split out of enforce_and_record_usage so a caller can check first
    and record only once it knows the record is warranted -- e.g. after
    a synchronous model call actually succeeds (a Gemini outage must
    not burn the user's monthly quota), or right before enqueueing the
    Celery job that does the real, expensive work later.

    CIN-158: `for_update=True` closes the check-then-insert race the
    same way enforce_and_record_usage does (see _check_limit) -- pass it
    ONLY when record_usage() for this same call is guaranteed to follow
    within the same request, with nothing slower than local DB/CPU work
    (no external API call, no `.delay()` that could itself block) in
    between. That's true for video_projects.py's illustrations/
    video-generation endpoints (record happens before the Celery
    `.delay()`, generation itself runs later in the worker) and for
    layout rendering (local Pillow, no network call at all) -- false for
    video_projects.py's script/brief endpoints, which call Gemini
    in-request between the check and the record; those keep the
    default and keep the pre-CIN-158 race, same tradeoff CIN-139
    originally chose.
    """
    _check_limit(db, user, event_type, content_type, count=count, for_update=for_update)


def record_usage(
    db: Session,
    user: User,
    event_type: UsageEventType,
    content_type: GenerationContentType | None = None,
    count: int = 1,
) -> None:
    """Record `count` usage events without re-checking the limit --
    pairs with check_usage_limit around a fallible operation."""
    db.add_all(
        [
            UsageEvent(user_id=user.id, event_type=event_type, content_type=content_type)
            for _ in range(count)
        ]
    )
    db.commit()


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

    CIN-158: for_update=True -- check and record happen in this one
    short transaction with no external call in between, so it's safe to
    serialize concurrent callers on the user's subscription row (see
    _check_limit for why that closes the race).
    """
    _check_limit(db, user, event_type, content_type, count=1, for_update=True)
    db.add(UsageEvent(user_id=user.id, event_type=event_type, content_type=content_type))
    db.commit()


def enforce_and_record_usage_bulk(
    db: Session,
    user: User,
    event_type: UsageEventType,
    count: int,
    content_type: GenerationContentType | None = None,
) -> None:
    """Same as `enforce_and_record_usage`, but for a fan-out publish
    (CIN-106) where one request creates `count` events at once (one
    per target account). The whole batch is checked as a single unit
    against the remaining limit -- either all `count` events fit, or
    none of them are recorded, rather than silently publishing some
    prefix of the requested targets and dropping the rest.

    CIN-158: for_update=True, same reasoning as enforce_and_record_usage.
    """
    _check_limit(db, user, event_type, content_type, count=count, for_update=True)
    db.add_all(
        [UsageEvent(user_id=user.id, event_type=event_type, content_type=content_type) for _ in range(count)]
    )
    db.commit()
