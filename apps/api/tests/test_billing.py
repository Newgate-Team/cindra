from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

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


def test_webhook_pay_activates_subscription(client: TestClient, db: Session) -> None:
    _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))

    response = client.post(
        "/billing/webhook/cloudpayments/pay", data={"AccountId": str(user.id)}
    )
    assert response.status_code == 200
    assert response.json() == {"code": 0}

    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    assert subscription.status == "active"
    assert subscription.store == "cloudpayments"


def test_webhook_fail_marks_past_due(client: TestClient, db: Session) -> None:
    _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))

    client.post("/billing/webhook/cloudpayments/fail", data={"AccountId": str(user.id)})

    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    assert subscription.status == "past_due"


def test_webhook_cancel_marks_canceled(client: TestClient, db: Session) -> None:
    _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))

    client.post("/billing/webhook/cloudpayments/cancel", data={"AccountId": str(user.id)})

    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    assert subscription.status == "canceled"


def test_webhook_recurrent_success_activates(client: TestClient, db: Session) -> None:
    _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))

    client.post(
        "/billing/webhook/cloudpayments/recurrent",
        data={"AccountId": str(user.id), "Status": "Completed"},
    )

    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    assert subscription.status == "active"


def test_webhook_recurrent_failure_marks_past_due(client: TestClient, db: Session) -> None:
    _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))

    client.post(
        "/billing/webhook/cloudpayments/recurrent",
        data={"AccountId": str(user.id), "Status": "Declined"},
    )

    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    assert subscription.status == "past_due"


def test_webhook_unknown_account_id_still_returns_code_0(client: TestClient) -> None:
    response = client.post(
        "/billing/webhook/cloudpayments/pay", data={"AccountId": "does-not-exist"}
    )
    assert response.status_code == 200
    assert response.json() == {"code": 0}


def test_webhook_unknown_event_type_returns_code_0_without_error(
    client: TestClient, db: Session
) -> None:
    _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))

    response = client.post(
        "/billing/webhook/cloudpayments/check", data={"AccountId": str(user.id)}
    )
    assert response.status_code == 200
    assert response.json() == {"code": 0}
