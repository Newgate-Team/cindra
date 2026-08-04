from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Subscription, User


def _auth_headers(client: TestClient) -> dict[str, str]:
    payload = {"email": "ada@cindra.dev", "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    token = client.post("/auth/login", json=payload).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_register_creates_free_subscription(client: TestClient, db: Session) -> None:
    _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    assert subscription is not None
    assert subscription.tier == "free"
    assert subscription.status == "active"


def test_get_subscription_endpoint(client: TestClient) -> None:
    headers = _auth_headers(client)
    response = client.get("/billing/subscription", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "free"
    assert body["status"] == "active"
    assert body["current_period_end"] is None


def test_get_subscription_requires_auth(client: TestClient) -> None:
    assert client.get("/billing/subscription").status_code == 401


@pytest.fixture
def _pro_plan_id(monkeypatch: pytest.MonkeyPatch) -> str:
    plan_id = "P-TEST-PRO"
    monkeypatch.setattr(get_settings(), "paypal_pro_plan_id", plan_id)
    return plan_id


def test_confirm_subscription_activates_pro_tier(
    client: TestClient, db: Session, _pro_plan_id: str
) -> None:
    headers = _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))

    with patch(
        "app.billing_integrations.paypal.get_subscription",
        return_value={"custom_id": str(user.id), "plan_id": _pro_plan_id, "status": "ACTIVE"},
    ):
        response = client.post(
            "/billing/paypal/confirm-subscription",
            json={"subscription_id": "I-FAKE123"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "pro"
    assert body["status"] == "active"

    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    assert subscription.store == "paypal"


def test_confirm_subscription_for_someone_elses_paypal_account_returns_403(
    client: TestClient, db: Session, _pro_plan_id: str
) -> None:
    headers = _auth_headers(client)

    with patch(
        "app.billing_integrations.paypal.get_subscription",
        return_value={
            "custom_id": "11111111-1111-1111-1111-111111111111",
            "plan_id": _pro_plan_id,
            "status": "ACTIVE",
        },
    ):
        response = client.post(
            "/billing/paypal/confirm-subscription",
            json={"subscription_id": "I-FAKE123"},
            headers=headers,
        )

    assert response.status_code == 403


def test_confirm_subscription_unknown_plan_returns_400(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))

    with patch(
        "app.billing_integrations.paypal.get_subscription",
        return_value={"custom_id": str(user.id), "plan_id": "P-UNKNOWN", "status": "ACTIVE"},
    ):
        response = client.post(
            "/billing/paypal/confirm-subscription",
            json={"subscription_id": "I-FAKE123"},
            headers=headers,
        )

    assert response.status_code == 400


def test_confirm_subscription_paypal_failure_returns_502(client: TestClient) -> None:
    headers = _auth_headers(client)

    with patch(
        "app.billing_integrations.paypal.get_subscription", side_effect=RuntimeError("boom")
    ):
        response = client.post(
            "/billing/paypal/confirm-subscription",
            json={"subscription_id": "I-FAKE123"},
            headers=headers,
        )

    assert response.status_code == 502


def test_confirm_subscription_requires_auth(client: TestClient) -> None:
    response = client.post(
        "/billing/paypal/confirm-subscription", json={"subscription_id": "I-FAKE123"}
    )
    assert response.status_code == 401


def test_webhook_activated_sets_active(client: TestClient, db: Session) -> None:
    _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))

    response = client.post(
        "/billing/webhook/paypal",
        json={
            "event_type": "BILLING.SUBSCRIPTION.ACTIVATED",
            "resource": {"custom_id": str(user.id)},
        },
    )
    assert response.status_code == 200

    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    assert subscription.status == "active"
    assert subscription.store == "paypal"


def test_webhook_suspended_marks_past_due(client: TestClient, db: Session) -> None:
    _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))

    client.post(
        "/billing/webhook/paypal",
        json={
            "event_type": "BILLING.SUBSCRIPTION.SUSPENDED",
            "resource": {"custom_id": str(user.id)},
        },
    )

    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    assert subscription.status == "past_due"


def test_webhook_cancelled_marks_canceled(client: TestClient, db: Session) -> None:
    _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))

    client.post(
        "/billing/webhook/paypal",
        json={
            "event_type": "BILLING.SUBSCRIPTION.CANCELLED",
            "resource": {"custom_id": str(user.id)},
        },
    )

    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    assert subscription.status == "canceled"


def test_webhook_unknown_custom_id_still_returns_200(client: TestClient) -> None:
    response = client.post(
        "/billing/webhook/paypal",
        json={
            "event_type": "BILLING.SUBSCRIPTION.ACTIVATED",
            "resource": {"custom_id": "does-not-exist"},
        },
    )
    assert response.status_code == 200


def test_webhook_unknown_event_type_returns_200_without_error(
    client: TestClient, db: Session
) -> None:
    _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))

    response = client.post(
        "/billing/webhook/paypal",
        json={"event_type": "SOME.OTHER.EVENT", "resource": {"custom_id": str(user.id)}},
    )
    assert response.status_code == 200
