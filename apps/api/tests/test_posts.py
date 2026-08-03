from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Post, PostStatus, SocialPlatform, User
from app.scheduler import registry
from app.scheduler.registry import register_publisher
from app.social_accounts import upsert_social_account


@pytest.fixture(autouse=True)
def _fake_telegram_publisher():
    previous = registry._REGISTRY.get(SocialPlatform.telegram)
    register_publisher(SocialPlatform.telegram, lambda account, post: {"message_id": 7})
    yield
    if previous is not None:
        register_publisher(SocialPlatform.telegram, previous)


def _auth_headers(client: TestClient) -> dict[str, str]:
    payload = {"email": "ada@cindra.dev", "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    token = client.post("/auth/login", json=payload).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _connected_account_id(client: TestClient, headers: dict[str, str], db: Session) -> str:
    from unittest.mock import patch

    with (
        patch(
            "app.routers.social_accounts.get_chat",
            return_value={"id": -100123, "title": "My Channel"},
        ),
        patch(
            "app.routers.social_accounts.get_me",
            return_value={"id": 999, "username": "cindra_bot"},
        ),
        patch(
            "app.routers.social_accounts.get_chat_member",
            return_value={"status": "member"},
        ),
    ):
        response = client.post(
            "/social-accounts/telegram/connect", json={"chat_id": "@mychannel"}, headers=headers
        )
    return response.json()["id"]


def test_create_post_without_scheduled_for_publishes_immediately(
    client: TestClient, db: Session
) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)

    response = client.post(
        "/posts",
        json={"social_account_id": account_id, "text": "Готовый пост"},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "published"
    assert body["platform_message_id"] == "7"


def test_create_post_scheduled_in_future_stays_scheduled(
    client: TestClient, db: Session
) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()

    response = client.post(
        "/posts",
        json={"social_account_id": account_id, "text": "Пост на потом", "scheduled_for": future},
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["status"] == "scheduled"


def test_create_post_for_someone_elses_account_returns_404(
    client: TestClient, db: Session
) -> None:
    other = User(email="eve@cindra.dev", hashed_password="x")
    db.add(other)
    db.commit()
    other_account = upsert_social_account(
        db, other, SocialPlatform.telegram, "-999", access_token="t"
    )

    headers = _auth_headers(client)
    response = client.post(
        "/posts",
        json={"social_account_id": str(other_account.id), "text": "тест"},
        headers=headers,
    )
    assert response.status_code == 404


def test_get_post(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    created = client.post(
        "/posts", json={"social_account_id": account_id, "text": "тест"}, headers=headers
    ).json()

    response = client.get(f"/posts/{created['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_list_posts_scoped_to_owner(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    client.post(
        "/posts", json={"social_account_id": account_id, "text": "мой пост"}, headers=headers
    )

    other = User(email="eve2@cindra.dev", hashed_password="x")
    db.add(other)
    db.commit()
    other_account = upsert_social_account(
        db, other, SocialPlatform.telegram, "-777", access_token="t"
    )
    db.add(
        Post(
            user_id=other.id,
            social_account_id=other_account.id,
            text="чужой пост",
            status=PostStatus.published,
            scheduled_for=datetime.now(UTC),
        )
    )
    db.commit()

    response = client.get("/posts", headers=headers)
    assert response.status_code == 200
    texts = [p["text"] for p in response.json()]
    assert texts == ["мой пост"]


def test_list_posts_requires_auth(client: TestClient) -> None:
    assert client.get("/posts").status_code == 401


def test_create_post_requires_auth(client: TestClient) -> None:
    response = client.post("/posts", json={"social_account_id": "x", "text": "тест"})
    assert response.status_code == 401


def test_create_post_returns_402_once_publication_limit_reached(
    client: TestClient, db: Session
) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)

    for _ in range(10):  # free tier limit, see app/plans.py
        response = client.post(
            "/posts", json={"social_account_id": account_id, "text": "тест"}, headers=headers
        )
        assert response.status_code == 201

    response = client.post(
        "/posts", json={"social_account_id": account_id, "text": "тест"}, headers=headers
    )
    assert response.status_code == 402
