import uuid

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.security import (
    create_access_token,
    create_meta_oauth_state,
    create_telegram_verification_token,
    create_tiktok_oauth_state,
    decode_access_token,
)


def test_decode_access_token_accepts_a_real_access_token() -> None:
    user_id = uuid.uuid4()
    assert decode_access_token(create_access_token(user_id)) == user_id


def test_decode_access_token_rejects_tiktok_oauth_state() -> None:
    # CIN-157: create_tiktok_oauth_state also puts `sub` in its payload
    # (needed for its own decode_tiktok_oauth_state), so without a
    # `typ` check this would silently work as a full access token.
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(create_tiktok_oauth_state(uuid.uuid4()))


def test_decode_access_token_rejects_meta_oauth_state() -> None:
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(create_meta_oauth_state(uuid.uuid4()))


def test_decode_access_token_rejects_telegram_verification_token() -> None:
    # This one has no `sub` claim at all, so it already failed with a
    # KeyError before CIN-157 -- kept here as a regression guard so a
    # future payload change can't quietly reopen the same class of gap.
    with pytest.raises((jwt.InvalidTokenError, KeyError)):
        decode_access_token(create_telegram_verification_token("-100123", "123456"))


def _auth_headers(client: TestClient) -> dict[str, str]:
    payload = {"email": "cin157@cindra.dev", "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    token = client.post("/auth/login", json=payload).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_instagram_oauth_state_cannot_authenticate_as_bearer_token(
    client: TestClient, db: Session
) -> None:
    # End-to-end reproduction of CIN-157: a state token minted for the
    # Instagram OAuth dialog (which travels through Meta's URL/consent
    # flow, not just our own Authorization header) must not double as
    # a session token.
    headers = _auth_headers(client)
    state = client.post("/social-accounts/instagram/start", headers=headers).json()["state"]

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {state}"})
    assert response.status_code == 401


def test_tiktok_oauth_state_cannot_authenticate_as_bearer_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "tiktok_client_key", "client-key")
    monkeypatch.setattr(get_settings(), "tiktok_client_secret", "client-secret")

    headers = _auth_headers(client)
    auth_url = client.post("/social-accounts/tiktok/start", headers=headers).json()[
        "authorization_url"
    ]
    state = auth_url.split("state=")[1].split("&")[0]
    from urllib.parse import unquote

    state = unquote(state)

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {state}"})
    assert response.status_code == 401
