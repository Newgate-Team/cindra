from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import SocialPlatform, User
from app.social_accounts import get_access_token, upsert_social_account
from app.social_integrations.errors import PermanentPublishError
from app.token_crypto import decrypt_token, encrypt_token


def test_token_encryption_roundtrip() -> None:
    ciphertext = encrypt_token("super-secret-access-token")
    assert ciphertext != "super-secret-access-token"
    assert decrypt_token(ciphertext) == "super-secret-access-token"


def test_upsert_social_account_encrypts_token_at_rest(db: Session, user: User) -> None:
    account = upsert_social_account(
        db,
        user,
        platform=SocialPlatform.telegram,
        external_account_id="12345",
        access_token="raw-bot-token",
        display_name="@my_channel",
    )
    assert account.encrypted_access_token != "raw-bot-token"
    assert get_access_token(account) == "raw-bot-token"


def test_upsert_is_idempotent_per_platform_and_account(db: Session, user: User) -> None:
    first = upsert_social_account(
        db, user, SocialPlatform.telegram, "12345", access_token="token-1"
    )
    second = upsert_social_account(
        db, user, SocialPlatform.telegram, "12345", access_token="token-2"
    )
    assert first.id == second.id
    assert get_access_token(second) == "token-2"


def _auth_headers(client: TestClient) -> dict[str, str]:
    payload = {"email": "ada@cindra.dev", "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    token = client.post("/auth/login", json=payload).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_list_social_accounts_excludes_tokens(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    me = client.get("/auth/me", headers=headers).json()

    upsert_social_account(
        db,
        db.get(User, me["id"]),
        SocialPlatform.instagram,
        "ig-42",
        access_token="ig-secret",
        token_expires_at=datetime.now(UTC) + timedelta(days=60),
        display_name="@brand",
    )

    response = client.get("/social-accounts", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["platform"] == "instagram"
    assert body[0]["external_account_id"] == "ig-42"
    assert "access_token" not in body[0]
    assert "encrypted_access_token" not in body[0]


def test_disconnect_social_account(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    me = client.get("/auth/me", headers=headers).json()
    account = upsert_social_account(
        db, db.get(User, me["id"]), SocialPlatform.telegram, "12345", access_token="t"
    )

    delete_response = client.delete(f"/social-accounts/{account.id}", headers=headers)
    assert delete_response.status_code == 204
    assert client.get("/social-accounts", headers=headers).json() == []


def test_disconnect_social_account_not_owned_returns_404(
    client: TestClient, db: Session
) -> None:
    other = User(email="eve@cindra.dev", hashed_password="x")
    db.add(other)
    db.commit()
    other_account = upsert_social_account(
        db, other, SocialPlatform.telegram, "999", access_token="t"
    )

    headers = _auth_headers(client)
    response = client.delete(f"/social-accounts/{other_account.id}", headers=headers)
    assert response.status_code == 404


def test_connect_telegram_creates_social_account(client: TestClient) -> None:
    headers = _auth_headers(client)
    with patch(
        "app.routers.social_accounts.get_chat",
        return_value={"id": -100123, "title": "My Channel"},
    ):
        response = client.post(
            "/social-accounts/telegram/connect", json={"chat_id": "@mychannel"}, headers=headers
        )

    assert response.status_code == 201
    body = response.json()
    assert body["platform"] == "telegram"
    assert body["external_account_id"] == "-100123"
    assert body["display_name"] == "My Channel"

    listed = client.get("/social-accounts", headers=headers).json()
    assert len(listed) == 1


def test_connect_telegram_bad_chat_returns_400(client: TestClient) -> None:
    headers = _auth_headers(client)
    with patch(
        "app.routers.social_accounts.get_chat",
        side_effect=PermanentPublishError("chat not found"),
    ):
        response = client.post(
            "/social-accounts/telegram/connect", json={"chat_id": "@doesnotexist"}, headers=headers
        )

    assert response.status_code == 400
    assert "chat not found" in response.json()["detail"]


def test_connect_telegram_requires_auth(client: TestClient) -> None:
    response = client.post("/social-accounts/telegram/connect", json={"chat_id": "@mychannel"})
    assert response.status_code == 401


def test_connect_instagram_creates_social_account(client: TestClient) -> None:
    headers = _auth_headers(client)
    with (
        patch(
            "app.routers.social_accounts.instagram.exchange_code_for_token",
            return_value="short-lived",
        ),
        patch(
            "app.routers.social_accounts.instagram.get_long_lived_token",
            return_value="long-lived",
        ),
        patch(
            "app.routers.social_accounts.instagram.discover_instagram_business_account",
            return_value={"id": "ig-42", "username": "mybrand"},
        ),
    ):
        response = client.post(
            "/social-accounts/instagram/connect", json={"code": "auth-code"}, headers=headers
        )

    assert response.status_code == 201
    body = response.json()
    assert body["platform"] == "instagram"
    assert body["external_account_id"] == "ig-42"
    assert body["display_name"] == "mybrand"


def test_connect_instagram_no_linked_account_returns_400(client: TestClient) -> None:
    headers = _auth_headers(client)
    with (
        patch(
            "app.routers.social_accounts.instagram.exchange_code_for_token",
            return_value="short-lived",
        ),
        patch(
            "app.routers.social_accounts.instagram.get_long_lived_token",
            return_value="long-lived",
        ),
        patch(
            "app.routers.social_accounts.instagram.discover_instagram_business_account",
            side_effect=PermanentPublishError("нет привязанного Instagram-аккаунта"),
        ),
    ):
        response = client.post(
            "/social-accounts/instagram/connect", json={"code": "auth-code"}, headers=headers
        )

    assert response.status_code == 400
    assert "нет привязанного" in response.json()["detail"]


def test_connect_instagram_requires_auth(client: TestClient) -> None:
    response = client.post("/social-accounts/instagram/connect", json={"code": "auth-code"})
    assert response.status_code == 401
