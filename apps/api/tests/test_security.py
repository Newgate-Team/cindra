import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import User
from app.security import (
    LOGIN_LOCKOUT_MINUTES,
    MAX_FAILED_LOGIN_ATTEMPTS,
    create_access_token,
    create_meta_oauth_state,
    create_telegram_verification_token,
    create_tiktok_oauth_state,
    decode_access_token,
    is_locked_out,
    record_failed_login,
    record_successful_login,
)


def test_decode_access_token_accepts_a_real_access_token() -> None:
    user_id = uuid.uuid4()
    assert decode_access_token(create_access_token(user_id)) == user_id


def _user(failed_login_attempts: int = 0, locked_until: datetime | None = None) -> User:
    return User(
        email="lockout-test@cindra.dev",
        failed_login_attempts=failed_login_attempts,
        locked_until=locked_until,
    )


def test_is_locked_out_false_by_default() -> None:
    assert is_locked_out(_user()) is False


def test_is_locked_out_true_while_locked_until_is_in_the_future() -> None:
    assert is_locked_out(_user(locked_until=datetime.now(UTC) + timedelta(minutes=5))) is True


def test_is_locked_out_false_once_locked_until_is_in_the_past() -> None:
    assert is_locked_out(_user(locked_until=datetime.now(UTC) - timedelta(seconds=1))) is False


def test_record_failed_login_increments_counter() -> None:
    user = _user(failed_login_attempts=2)
    record_failed_login(user)
    assert user.failed_login_attempts == 3
    assert user.locked_until is None


def test_record_failed_login_locks_out_at_threshold() -> None:
    user = _user(failed_login_attempts=MAX_FAILED_LOGIN_ATTEMPTS - 1)
    record_failed_login(user)
    assert user.failed_login_attempts == MAX_FAILED_LOGIN_ATTEMPTS
    assert user.locked_until is not None
    assert user.locked_until <= datetime.now(UTC) + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)


def test_record_failed_login_does_not_extend_an_active_lockout() -> None:
    # CIN-159: continuing to guess during lockout must not push
    # locked_until further out -- that would let an attacker keep the
    # real owner locked out indefinitely just by not stopping.
    original_lock = datetime.now(UTC) + timedelta(minutes=1)
    user = _user(failed_login_attempts=MAX_FAILED_LOGIN_ATTEMPTS, locked_until=original_lock)
    record_failed_login(user)
    assert user.locked_until == original_lock
    assert user.failed_login_attempts == MAX_FAILED_LOGIN_ATTEMPTS


def test_record_successful_login_clears_lockout_state() -> None:
    user = _user(
        failed_login_attempts=MAX_FAILED_LOGIN_ATTEMPTS,
        locked_until=datetime.now(UTC) + timedelta(minutes=5),
    )
    record_successful_login(user)
    assert user.failed_login_attempts == 0
    assert user.locked_until is None


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
