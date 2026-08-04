import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing_integrations import paypal
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import (
    Subscription,
    SubscriptionStatus,
    SubscriptionStore,
    SubscriptionTier,
    User,
)
from app.schemas import ConfirmSubscriptionRequest, SubscriptionOut

router = APIRouter(prefix="/billing", tags=["billing"])

# PayPal subscription-lifecycle event types that update our status --
# names confirmed as real, well-documented PayPal webhook event types
# (not guessed); anything else is a no-op, same defensive fallback the
# old CloudPayments handler used for unrecognized event types.
_ACTIVE_EVENTS = {"BILLING.SUBSCRIPTION.ACTIVATED", "BILLING.SUBSCRIPTION.RE-ACTIVATED"}
_PAST_DUE_EVENTS = {"BILLING.SUBSCRIPTION.SUSPENDED", "BILLING.SUBSCRIPTION.PAYMENT.FAILED"}
_CANCELED_EVENTS = {"BILLING.SUBSCRIPTION.CANCELLED", "BILLING.SUBSCRIPTION.EXPIRED"}


def _tier_for_plan_id(plan_id: str) -> SubscriptionTier | None:
    if not plan_id:
        return None
    settings = get_settings()
    if plan_id == settings.paypal_pro_plan_id:
        return SubscriptionTier.pro
    if plan_id == settings.paypal_business_plan_id:
        return SubscriptionTier.business
    return None


@router.get("/subscription", response_model=SubscriptionOut)
def get_subscription(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Subscription:
    return db.scalar(select(Subscription).where(Subscription.user_id == current_user.id))


@router.post("/paypal/confirm-subscription", response_model=SubscriptionOut)
def confirm_paypal_subscription(
    payload: ConfirmSubscriptionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Subscription:
    """Called by the frontend right after the PayPal Buttons SDK's
    onApprove fires (CIN-87). Never trusts the client-supplied
    subscription_id directly -- fetches it from PayPal itself and
    checks `custom_id` matches the authenticated user before touching
    anything, exactly the same "don't trust client data" posture the
    old CloudPayments webhook applied to AccountId.
    """
    try:
        remote = paypal.get_subscription(payload.subscription_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Не удалось подтвердить подписку в PayPal"
        ) from exc

    if remote.get("custom_id") != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Подписка принадлежит другому пользователю"
        )

    tier = _tier_for_plan_id(remote.get("plan_id", ""))
    if tier is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неизвестный тариф")

    subscription = db.scalar(select(Subscription).where(Subscription.user_id == current_user.id))
    subscription.tier = tier
    subscription.status = (
        SubscriptionStatus.active if remote.get("status") == "ACTIVE" else SubscriptionStatus.past_due
    )
    subscription.store = SubscriptionStore.paypal
    db.commit()
    db.refresh(subscription)
    return subscription


_SIGNATURE_HEADERS = {
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}


@router.post("/webhook/paypal")
async def paypal_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    """Receives BILLING.SUBSCRIPTION.* lifecycle notifications from
    PayPal (see CIN-85). The event's `resource` is the same
    Subscription object returned by GET /v1/billing/subscriptions/{id}
    (confirmed against PayPal's official OpenAPI spec, not guessed --
    see billing_integrations/paypal.py), so `resource.custom_id` is the
    user id set at subscription-creation time -- same lookup pattern
    the old CloudPayments handler used for AccountId.

    Verifies authenticity via PayPal's own verify-webhook-signature
    endpoint (CIN-86) before touching anything -- fails closed (400) on
    a missing header, unconfigured PAYPAL_WEBHOOK_ID, a failed PayPal
    call, or a signature PayPal itself doesn't confirm. This closes the
    gap the old CloudPayments handler explicitly could not close (see
    CIN-79 history) -- PayPal's mechanism is publicly documented, so
    there was no reason to ship it unverified the way CloudPayments was.

    Once verified, always returns 200 (PayPal retries on non-2xx) even
    when the account isn't found or the event type isn't one we track
    -- neither is something retrying fixes.
    """
    body = await request.json()
    try:
        headers = {
            field: request.headers[header_name]
            for field, header_name in _SIGNATURE_HEADERS.items()
        }
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Отсутствует заголовок {exc.args[0]}"
        ) from None

    try:
        verified = paypal.verify_webhook_signature(webhook_event=body, **headers)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Не удалось проверить подпись вебхука"
        ) from exc
    if not verified:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверная подпись вебхука")

    event_type = body.get("event_type")
    resource = body.get("resource", {})
    custom_id = resource.get("custom_id")

    try:
        user_uuid = uuid.UUID(str(custom_id)) if custom_id else None
    except ValueError:
        user_uuid = None

    subscription = (
        db.scalar(select(Subscription).where(Subscription.user_id == user_uuid))
        if user_uuid is not None
        else None
    )
    if subscription is None:
        return {"status": "ok"}

    if event_type in _ACTIVE_EVENTS:
        subscription.status = SubscriptionStatus.active
    elif event_type in _PAST_DUE_EVENTS:
        subscription.status = SubscriptionStatus.past_due
    elif event_type in _CANCELED_EVENTS:
        subscription.status = SubscriptionStatus.canceled
    else:
        return {"status": "ok"}

    subscription.store = SubscriptionStore.paypal
    db.commit()
    return {"status": "ok"}
