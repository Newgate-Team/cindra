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


def _scheduled_post(client: TestClient, headers: dict[str, str], db: Session) -> dict:
    account_id = _connected_account_id(client, headers, db)
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    return client.post(
        "/posts",
        json={"social_account_id": account_id, "text": "черновик", "scheduled_for": future},
        headers=headers,
    ).json()


def test_update_post_changes_text_and_scheduled_for(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    post = _scheduled_post(client, headers, db)
    new_time = (datetime.now(UTC) + timedelta(days=1)).isoformat()

    response = client.patch(
        f"/posts/{post['id']}",
        json={"text": "новый текст", "scheduled_for": new_time},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "новый текст"
    assert body["status"] == "scheduled"


def test_update_post_partial_only_changes_provided_fields(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    post = _scheduled_post(client, headers, db)

    response = client.patch(
        f"/posts/{post['id']}", json={"text": "только текст поменялся"}, headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "только текст поменялся"
    assert body["scheduled_for"] == post["scheduled_for"]


def test_update_already_published_post_returns_400(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    published = client.post(
        "/posts", json={"social_account_id": account_id, "text": "уже вышел"}, headers=headers
    ).json()
    assert published["status"] == "published"

    response = client.patch(
        f"/posts/{published['id']}", json={"text": "поздно"}, headers=headers
    )
    assert response.status_code == 400


def test_update_someone_elses_post_returns_404(client: TestClient, db: Session) -> None:
    other = User(email="eve3@cindra.dev", hashed_password="x")
    db.add(other)
    db.commit()
    other_account = upsert_social_account(
        db, other, SocialPlatform.telegram, "-555", access_token="t"
    )
    other_post = Post(
        user_id=other.id,
        social_account_id=other_account.id,
        text="чужой",
        status=PostStatus.scheduled,
        scheduled_for=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add(other_post)
    db.commit()

    headers = _auth_headers(client)
    response = client.patch(
        f"/posts/{other_post.id}", json={"text": "хочу поменять чужое"}, headers=headers
    )
    assert response.status_code == 404


def test_cancel_scheduled_post_deletes_it(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    post = _scheduled_post(client, headers, db)

    response = client.delete(f"/posts/{post['id']}", headers=headers)
    assert response.status_code == 204
    assert client.get(f"/posts/{post['id']}", headers=headers).status_code == 404


def test_cancel_already_published_post_returns_400(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    published = client.post(
        "/posts", json={"social_account_id": account_id, "text": "уже вышел"}, headers=headers
    ).json()

    response = client.delete(f"/posts/{published['id']}", headers=headers)
    assert response.status_code == 400
