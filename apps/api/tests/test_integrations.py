from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations import get_secret
from app.models import Integration, IntegrationProvider, User
from app.tool_integrations import heygen

_HEYGEN_USER = {
    "username": "jane",
    "email": "jane@example.com",
    "billing_type": "wallet",
    "wallet": {"currency": "usd", "remaining_balance": 12.5},
}


def _register_and_login(client: TestClient, email: str, role: str = "agency") -> dict[str, str]:
    payload = {"email": email, "password": "supersecret1", "role": role}
    client.post("/auth/register", json=payload)
    token = client.post(
        "/auth/login", json={"email": email, "password": "supersecret1"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _connect(client: TestClient, headers: dict[str, str], api_key: str = "hg-secret-key"):
    with patch(
        "app.routers.integrations.heygen.get_current_user", return_value=_HEYGEN_USER
    ) as validate:
        response = client.put("/integrations/heygen", json={"api_key": api_key}, headers=headers)
    return response, validate


def _rows(db: Session) -> list[Integration]:
    db.expire_all()
    return list(db.scalars(select(Integration)))


def test_connect_validates_then_stores_key_encrypted(client: TestClient, db: Session) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")

    response, validate = _connect(client, headers, api_key="  hg-secret-key\n")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "heygen"
    assert body["account_label"] == "jane@example.com"
    assert body["is_own"] is True
    assert "hg-secret-key" not in response.text
    # Stripped before validation, so a pasted trailing newline isn't
    # reported as an invalid key.
    validate.assert_called_once_with("hg-secret-key")

    (row,) = _rows(db)
    assert row.encrypted_secret != "hg-secret-key"
    assert get_secret(row) == "hg-secret-key"


def test_connect_with_rejected_key_stores_nothing(client: TestClient, db: Session) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    with patch(
        "app.routers.integrations.heygen.get_current_user",
        side_effect=heygen.HeyGenAuthError("Invalid or expired API key"),
    ):
        response = client.put(
            "/integrations/heygen", json={"api_key": "wrong-key-123"}, headers=headers
        )

    assert response.status_code == 400
    assert "не принял этот ключ" in response.json()["detail"]
    assert _rows(db) == []


def test_connect_when_heygen_is_down_returns_503_and_stores_nothing(
    client: TestClient, db: Session
) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    with patch(
        "app.routers.integrations.heygen.get_current_user",
        side_effect=heygen.HeyGenTransientError("HeyGen 503"),
    ):
        response = client.put(
            "/integrations/heygen", json={"api_key": "hg-secret-key"}, headers=headers
        )

    assert response.status_code == 503
    assert _rows(db) == []


def test_reconnecting_replaces_the_key_in_the_same_row(client: TestClient, db: Session) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    _connect(client, headers, api_key="first-key-123")
    _connect(client, headers, api_key="second-key-456")

    (row,) = _rows(db)
    assert get_secret(row) == "second-key-456"


def test_list_and_disconnect(client: TestClient, db: Session) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    assert client.get("/integrations", headers=headers).json() == []

    _connect(client, headers)
    listed = client.get("/integrations", headers=headers).json()
    assert [item["provider"] for item in listed] == ["heygen"]
    assert "encrypted_secret" not in listed[0]

    assert client.delete("/integrations/heygen", headers=headers).status_code == 204
    assert client.get("/integrations", headers=headers).json() == []
    assert client.delete("/integrations/heygen", headers=headers).status_code == 404


def test_avatars_and_voices_require_a_connection(client: TestClient) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    assert client.get("/integrations/heygen/avatars", headers=headers).status_code == 404
    assert client.get("/integrations/heygen/voices", headers=headers).status_code == 404
    assert client.get("/integrations/heygen/account", headers=headers).status_code == 404


def test_avatars_are_fetched_with_the_stored_key(client: TestClient) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    _connect(client, headers, api_key="hg-secret-key")
    page = {
        "items": [
            {
                "id": "lk_1",
                "name": "Look",
                "avatar_type": "digital_twin",
                "gender": None,
                "preview_image_url": "https://files.heygen.ai/look/1.jpg",
                "preview_video_url": None,
                "default_voice_id": "voice-1",
            }
        ],
        "next_token": "next",
    }
    with patch(
        "app.routers.integrations.heygen.list_avatar_looks", return_value=page
    ) as list_looks:
        response = client.get(
            "/integrations/heygen/avatars?ownership=private&token=abc", headers=headers
        )

    assert response.status_code == 200
    assert response.json() == page
    list_looks.assert_called_once_with("hg-secret-key", token="abc", ownership="private")


def test_voices_are_fetched_with_filters(client: TestClient) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    _connect(client, headers, api_key="hg-secret-key")
    with patch(
        "app.routers.integrations.heygen.list_voices",
        return_value={"items": [], "next_token": None},
    ) as list_voices:
        response = client.get(
            "/integrations/heygen/voices?language=Russian&gender=female", headers=headers
        )

    assert response.status_code == 200
    list_voices.assert_called_once_with(
        "hg-secret-key", token=None, language="Russian", gender="female"
    )


def test_account_reports_wallet_balance(client: TestClient) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    _connect(client, headers)
    with patch("app.routers.integrations.heygen.get_current_user", return_value=_HEYGEN_USER):
        response = client.get("/integrations/heygen/account", headers=headers)

    assert response.json() == {
        "account_label": "jane@example.com",
        "billing_type": "wallet",
        "remaining_balance": 12.5,
        "currency": "usd",
    }


def test_key_revoked_after_connecting_tells_the_user_to_reconnect(client: TestClient) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    _connect(client, headers)
    with patch(
        "app.routers.integrations.heygen.list_avatar_looks",
        side_effect=heygen.HeyGenAuthError("Invalid or expired API key"),
    ):
        response = client.get("/integrations/heygen/avatars", headers=headers)

    assert response.status_code == 400
    assert "подключите HeyGen заново" in response.json()["detail"]


def test_endpoints_require_auth(client: TestClient) -> None:
    assert client.get("/integrations").status_code == 401
    assert client.put("/integrations/heygen", json={"api_key": "hg-secret-key"}).status_code == 401
    assert client.delete("/integrations/heygen").status_code == 401
    assert client.get("/integrations/heygen/avatars").status_code == 401


def _make_team(client: TestClient, db: Session) -> tuple[dict[str, str], dict[str, str]]:
    owner_headers = _register_and_login(client, "owner@cindra.dev")
    team_id = client.post("/team", json={"name": "Команда"}, headers=owner_headers).json()["id"]
    member_headers = _register_and_login(client, "member@cindra.dev", role="solo")
    member = db.scalar(select(User).where(User.email == "member@cindra.dev"))
    member.team_id = team_id
    db.commit()
    return owner_headers, member_headers


def test_teammate_generates_with_the_owners_connection(client: TestClient, db: Session) -> None:
    owner_headers, member_headers = _make_team(client, db)
    _connect(client, owner_headers, api_key="owners-key-123")

    listed = client.get("/integrations", headers=member_headers).json()
    assert len(listed) == 1
    assert listed[0]["is_own"] is False

    with patch(
        "app.routers.integrations.heygen.list_voices",
        return_value={"items": [], "next_token": None},
    ) as list_voices:
        client.get("/integrations/heygen/voices", headers=member_headers)
    assert list_voices.call_args.args[0] == "owners-key-123"


def test_members_own_key_takes_precedence_over_the_owners(
    client: TestClient, db: Session
) -> None:
    owner_headers, member_headers = _make_team(client, db)
    _connect(client, owner_headers, api_key="owners-key-123")
    _connect(client, member_headers, api_key="members-key-456")

    assert len(_rows(db)) == 2
    with patch(
        "app.routers.integrations.heygen.list_voices",
        return_value={"items": [], "next_token": None},
    ) as list_voices:
        client.get("/integrations/heygen/voices", headers=member_headers)
    assert list_voices.call_args.args[0] == "members-key-456"


def test_outsider_cannot_see_or_use_another_teams_connection(
    client: TestClient, db: Session
) -> None:
    owner_headers, _member_headers = _make_team(client, db)
    _connect(client, owner_headers)
    outsider_headers = _register_and_login(client, "outsider@cindra.dev")

    assert client.get("/integrations", headers=outsider_headers).json() == []
    assert client.get("/integrations/heygen/voices", headers=outsider_headers).status_code == 404
    assert client.delete("/integrations/heygen", headers=outsider_headers).status_code == 404
    assert len(_rows(db)) == 1
    assert _rows(db)[0].provider == IntegrationProvider.heygen
