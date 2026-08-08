from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
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


@contextmanager
def _bot_is_member(status: str = "member"):
    with (
        patch(
            "app.routers.social_accounts.get_me",
            return_value={"id": 999, "username": "cindra_bot"},
        ),
        patch(
            "app.routers.social_accounts.get_chat_member",
            return_value={"status": status},
        ),
    ):
        yield


def _start_verification(client: TestClient, headers: dict[str, str], chat: dict, chat_id: str = "@mychannel"):
    """CIN-128: step 1 of the connect flow -- issues a code the caller
    must place in the chat's description before /telegram/connect will
    accept it. Returns (verification_token, chat_with_code) so tests
    can feed the latter back as get_chat's next return value, mirroring
    the real flow where the user has actually edited the description."""
    with patch("app.routers.social_accounts.get_chat", return_value=chat):
        response = client.post(
            "/social-accounts/telegram/start-verification",
            json={"chat_id": chat_id},
            headers=headers,
        )
    assert response.status_code == 200, response.json()
    body = response.json()
    chat_with_code = {**chat, "description": body["code"]}
    return body["verification_token"], chat_with_code


def test_start_telegram_verification_returns_code_and_token(client: TestClient) -> None:
    headers = _auth_headers(client)
    with patch(
        "app.routers.social_accounts.get_chat",
        return_value={"id": -100123, "title": "My Channel"},
    ):
        response = client.post(
            "/social-accounts/telegram/start-verification",
            json={"chat_id": "@mychannel"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["code"].startswith("cindra-verify-")
    assert body["chat_title"] == "My Channel"
    assert body["verification_token"]


def test_start_telegram_verification_normalizes_tme_link(client: TestClient) -> None:
    headers = _auth_headers(client)
    with patch(
        "app.routers.social_accounts.get_chat",
        return_value={"id": -100123, "title": "My Channel"},
    ) as mock_get_chat:
        response = client.post(
            "/social-accounts/telegram/start-verification",
            json={"chat_id": "https://t.me/mychannel"},
            headers=headers,
        )

    assert response.status_code == 200
    mock_get_chat.assert_called_once()
    assert mock_get_chat.call_args[0][0] == "@mychannel"


def test_start_telegram_verification_bad_chat_returns_400(client: TestClient) -> None:
    headers = _auth_headers(client)
    with patch(
        "app.routers.social_accounts.get_chat",
        side_effect=PermanentPublishError("chat not found"),
    ):
        response = client.post(
            "/social-accounts/telegram/start-verification",
            json={"chat_id": "@doesnotexist"},
            headers=headers,
        )

    assert response.status_code == 400
    assert "chat not found" in response.json()["detail"]


def test_start_telegram_verification_requires_auth(client: TestClient) -> None:
    response = client.post(
        "/social-accounts/telegram/start-verification", json={"chat_id": "@mychannel"}
    )
    assert response.status_code == 401


def test_connect_telegram_creates_social_account(client: TestClient) -> None:
    headers = _auth_headers(client)
    chat = {"id": -100123, "title": "My Channel"}
    token, chat_with_code = _start_verification(client, headers, chat)

    with (
        patch("app.routers.social_accounts.get_chat", return_value=chat_with_code),
        _bot_is_member(),
    ):
        response = client.post(
            "/social-accounts/telegram/connect",
            json={"verification_token": token},
            headers=headers,
        )

    assert response.status_code == 201
    body = response.json()
    assert body["platform"] == "telegram"
    assert body["external_account_id"] == "-100123"
    assert body["display_name"] == "My Channel"

    listed = client.get("/social-accounts", headers=headers).json()
    assert len(listed) == 1


def test_connect_telegram_code_missing_from_description_returns_400(client: TestClient) -> None:
    # CIN-128: the whole point -- if the description wasn't actually
    # edited (e.g. someone just guesses/replays a token without being
    # able to touch the channel), the connect must be rejected.
    headers = _auth_headers(client)
    chat = {"id": -100123, "title": "My Channel"}
    token, _chat_with_code = _start_verification(client, headers, chat)

    with (
        patch("app.routers.social_accounts.get_chat", return_value={**chat, "description": None}),
        _bot_is_member(),
    ):
        response = client.post(
            "/social-accounts/telegram/connect",
            json={"verification_token": token},
            headers=headers,
        )

    assert response.status_code == 400
    assert "не найден" in response.json()["detail"]
    assert client.get("/social-accounts", headers=headers).json() == []


def test_connect_telegram_wrong_code_in_description_returns_400(client: TestClient) -> None:
    headers = _auth_headers(client)
    chat = {"id": -100123, "title": "My Channel"}
    token, _chat_with_code = _start_verification(client, headers, chat)

    with (
        patch(
            "app.routers.social_accounts.get_chat",
            return_value={**chat, "description": "cindra-verify-not-the-real-code"},
        ),
        _bot_is_member(),
    ):
        response = client.post(
            "/social-accounts/telegram/connect",
            json={"verification_token": token},
            headers=headers,
        )

    assert response.status_code == 400


def test_connect_telegram_invalid_verification_token_returns_400(client: TestClient) -> None:
    headers = _auth_headers(client)
    response = client.post(
        "/social-accounts/telegram/connect",
        json={"verification_token": "not-a-real-token"},
        headers=headers,
    )
    assert response.status_code == 400
    assert "истёк" in response.json()["detail"] or "недействителен" in response.json()["detail"]


def test_connect_telegram_expired_verification_token_returns_400(client: TestClient) -> None:
    headers = _auth_headers(client)
    expired = jwt.encode(
        {
            "chat_id": "@mychannel",
            "code": "cindra-verify-aaaaaaaa",
            "exp": datetime.now(UTC) - timedelta(minutes=1),
            "typ": "telegram_verification",
        },
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    response = client.post(
        "/social-accounts/telegram/connect",
        json={"verification_token": expired},
        headers=headers,
    )
    assert response.status_code == 400


def test_connect_telegram_bot_not_member_returns_400_with_bot_username(
    client: TestClient,
) -> None:
    headers = _auth_headers(client)
    chat = {"id": -100123, "title": "My Channel"}
    token, chat_with_code = _start_verification(client, headers, chat)

    with (
        patch("app.routers.social_accounts.get_chat", return_value=chat_with_code),
        _bot_is_member(status="left"),
    ):
        response = client.post(
            "/social-accounts/telegram/connect",
            json={"verification_token": token},
            headers=headers,
        )

    assert response.status_code == 400
    assert "@cindra_bot" in response.json()["detail"]


def test_connect_telegram_bot_membership_check_error_treated_as_not_added(
    client: TestClient,
) -> None:
    headers = _auth_headers(client)
    chat = {"id": -100123, "title": "My Channel"}
    token, chat_with_code = _start_verification(client, headers, chat)

    with (
        patch("app.routers.social_accounts.get_chat", return_value=chat_with_code),
        patch(
            "app.routers.social_accounts.get_me",
            return_value={"id": 999, "username": "cindra_bot"},
        ),
        patch(
            "app.routers.social_accounts.get_chat_member",
            side_effect=PermanentPublishError("user not found"),
        ),
    ):
        response = client.post(
            "/social-accounts/telegram/connect",
            json={"verification_token": token},
            headers=headers,
        )

    assert response.status_code == 400
    assert "@cindra_bot" in response.json()["detail"]


def test_connect_telegram_requires_auth(client: TestClient) -> None:
    response = client.post(
        "/social-accounts/telegram/connect", json={"verification_token": "whatever"}
    )
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
            "app.routers.social_accounts.instagram.discover_connected_accounts",
            return_value={
                "instagram": {"id": "ig-42", "username": "mybrand"},
                "facebook_page": {"id": "page-1", "name": "Cindra", "access_token": "page-token"},
            },
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


def test_connect_instagram_also_connects_facebook_page(client: TestClient) -> None:
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
            "app.routers.social_accounts.instagram.discover_connected_accounts",
            return_value={
                "instagram": {"id": "ig-42", "username": "mybrand"},
                "facebook_page": {"id": "page-1", "name": "Cindra", "access_token": "page-token"},
            },
        ),
    ):
        client.post("/social-accounts/instagram/connect", json={"code": "auth-code"}, headers=headers)

    listed = client.get("/social-accounts", headers=headers).json()
    platforms = {account["platform"]: account for account in listed}
    assert set(platforms) == {"instagram", "facebook"}
    assert platforms["facebook"]["external_account_id"] == "page-1"
    assert platforms["facebook"]["display_name"] == "Cindra"


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
            "app.routers.social_accounts.instagram.discover_connected_accounts",
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
