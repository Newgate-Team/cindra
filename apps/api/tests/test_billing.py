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
