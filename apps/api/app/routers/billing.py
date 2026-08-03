import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import Subscription, SubscriptionStatus, SubscriptionStore, User
from app.schemas import SubscriptionOut

router = APIRouter(prefix="/billing", tags=["billing"])

# CloudPayments notification "Status" values (Recurrent notifications
# carry one; Pay/Fail/Cancel don't -- their meaning is the event type
# itself). See https://developers.cloudpayments.ru/en/#recurrent.
_RECURRENT_SUCCESS_STATUSES = {"Completed", "Authorized"}


@router.get("/subscription", response_model=SubscriptionOut)
def get_subscription(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Subscription:
    return db.scalar(select(Subscription).where(Subscription.user_id == current_user.id))


@router.post("/webhook/cloudpayments/{event_type}")
async def cloudpayments_webhook(
    event_type: str, request: Request, db: Session = Depends(get_db)
) -> dict[str, int]:
    """Receives Pay/Fail/Recurrent/Cancel notifications from CloudPayments
    (see CIN-20). `AccountId` is set to our user's id at subscription
    creation time, so it doubles as the lookup key here -- no separate
    external-id column needed.

    SECURITY GAP (see follow-up ticket, blocks going live): does not
    verify the notification's authenticity. CloudPayments docs
    reference a "Notification Validation" mechanism for standard
    webhooks, but the exact header/algorithm couldn't be confirmed via
    live documentation (only a different, unrelated X-Signature scheme
    for their "Payout" feature was reachable) -- rather than guess a
    security-critical check, this is left unverified and explicit
    rather than silently wrong. Anyone who knows this URL can currently
    flip a user's subscription status; do not deploy before closing
    this gap.

    Always returns {"code": 0} (CloudPayments' documented "accept, do
    not retry" response) even when the account isn't found -- an
    unknown AccountId is not something retrying fixes.
    """
    form = await request.form()
    account_id = form.get("AccountId")
    try:
        account_uuid = uuid.UUID(str(account_id)) if account_id else None
    except ValueError:
        account_uuid = None
    subscription = (
        db.scalar(select(Subscription).where(Subscription.user_id == account_uuid))
        if account_uuid is not None
        else None
    )
    if subscription is None:
        return {"code": 0}

    if event_type == "pay":
        subscription.status = SubscriptionStatus.active
    elif event_type == "fail":
        subscription.status = SubscriptionStatus.past_due
    elif event_type == "cancel":
        subscription.status = SubscriptionStatus.canceled
    elif event_type == "recurrent":
        recurrent_status = form.get("Status")
        subscription.status = (
            SubscriptionStatus.active
            if recurrent_status in _RECURRENT_SUCCESS_STATUSES
            else SubscriptionStatus.past_due
        )
    else:
        return {"code": 0}

    subscription.store = SubscriptionStore.cloudpayments
    db.commit()
    return {"code": 0}
