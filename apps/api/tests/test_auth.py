import threading
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.google_auth import GoogleAuthError
from app.models import Subscription, User


def test_register_creates_user(client: TestClient) -> None:
    response = client.post(
        "/auth/register", json={"email": "ada@cindra.dev", "password": "supersecret1"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ada@cindra.dev"
    assert body["role"] == "solo"
    assert "id" in body
    assert "hashed_password" not in body


def test_register_accepts_agency_role(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={"email": "agency@cindra.dev", "password": "supersecret1", "role": "agency"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "agency"


def test_register_defaults_share_generations_to_feed_by_role(client: TestClient) -> None:
    # Лента (CIN-109) is a shared feed by design -- but for an agency
    # that means an unreleased client campaign is visible to every other
    # user before the client sees it published. solo keeps the shared
    # default; agency starts opted out.
    solo = client.post(
        "/auth/register", json={"email": "solo@cindra.dev", "password": "supersecret1"}
    )
    assert solo.json()["share_generations_to_feed"] is True

    agency = client.post(
        "/auth/register",
        json={"email": "agency@cindra.dev", "password": "supersecret1", "role": "agency"},
    )
    assert agency.json()["share_generations_to_feed"] is False


def test_update_me_changes_role(client: TestClient) -> None:
    payload = {"email": "ada@cindra.dev", "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    token = client.post("/auth/login", json=payload).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.patch("/auth/me", json={"role": "agency"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["role"] == "agency"

    me_response = client.get("/auth/me", headers=headers)
    assert me_response.json()["role"] == "agency"


def test_update_me_role_only_does_not_reset_feed_sharing_choice(client: TestClient) -> None:
    # share_generations_to_feed has its own default, independent of
    # role -- a role-only PATCH (the only kind the frontend currently
    # sends, see UserUpdate) must not silently reset a choice the user
    # already made.
    payload = {"email": "agency@cindra.dev", "password": "supersecret1", "role": "agency"}
    client.post("/auth/register", json=payload)
    token = client.post(
        "/auth/login", json={"email": payload["email"], "password": payload["password"]}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    opt_in = client.patch(
        "/auth/me", json={"role": "agency", "share_generations_to_feed": True}, headers=headers
    )
    assert opt_in.json()["share_generations_to_feed"] is True

    role_only = client.patch("/auth/me", json={"role": "agency"}, headers=headers)
    assert role_only.json()["share_generations_to_feed"] is True


def test_update_me_can_toggle_feed_sharing(client: TestClient) -> None:
    payload = {"email": "ada@cindra.dev", "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    token = client.post("/auth/login", json=payload).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.patch(
        "/auth/me", json={"role": "solo", "share_generations_to_feed": False}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["share_generations_to_feed"] is False

    me_response = client.get("/auth/me", headers=headers)
    assert me_response.json()["share_generations_to_feed"] is False


def test_register_duplicate_email_conflicts(client: TestClient) -> None:
    payload = {"email": "ada@cindra.dev", "password": "supersecret1"}
    assert client.post("/auth/register", json=payload).status_code == 201
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 409


def test_concurrent_registration_for_the_same_email_does_not_500(
    client: TestClient, db: Session
) -> None:
    # Found alongside CIN-158/162: a race, not a security bug -- e.g. a
    # double-clicked submit. The UNIQUE constraint on email is the real
    # guard; without catching the resulting IntegrityError, the loser
    # of the race got an unhandled 500 instead of the same clean 409
    # the sequential case (above) already returns.
    payload = {"email": "race@cindra.dev", "password": "supersecret1"}
    results: list[int] = []
    barrier = threading.Barrier(2)

    def attempt() -> None:
        barrier.wait(timeout=5)
        results.append(client.post("/auth/register", json=payload).status_code)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(results) == [201, 409]
    assert len(list(db.scalars(select(User).where(User.email == payload["email"])))) == 1


def test_login_returns_token_and_me_resolves_it(client: TestClient) -> None:
    payload = {"email": "ada@cindra.dev", "password": "supersecret1"}
    client.post("/auth/register", json=payload)

    login_response = client.post("/auth/login", json=payload)
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    me_response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "ada@cindra.dev"


def test_login_wrong_password_rejected(client: TestClient) -> None:
    client.post(
        "/auth/register", json={"email": "ada@cindra.dev", "password": "supersecret1"}
    )
    response = client.post(
        "/auth/login", json={"email": "ada@cindra.dev", "password": "wrong-password"}
    )
    assert response.status_code == 401


def test_login_locks_out_after_five_failed_attempts(client: TestClient) -> None:
    # CIN-159: /auth/login previously had no brute-force protection at
    # all -- unlimited password guesses against a known email.
    payload = {"email": "ada@cindra.dev", "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    wrong = {"email": payload["email"], "password": "wrong-password"}

    for _ in range(5):
        response = client.post("/auth/login", json=wrong)
        assert response.status_code == 401

    locked = client.post("/auth/login", json=wrong)
    assert locked.status_code == 429


def test_login_lockout_rejects_the_correct_password_too(client: TestClient) -> None:
    # The lockout check must happen before password verification --
    # otherwise a correct guess mid-lockout would still return 200 and
    # leak "that was the password" to an attacker who is guessing.
    payload = {"email": "ada@cindra.dev", "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    wrong = {"email": payload["email"], "password": "wrong-password"}
    for _ in range(5):
        client.post("/auth/login", json=wrong)

    response = client.post("/auth/login", json=payload)
    assert response.status_code == 429


def test_login_success_resets_failed_attempt_counter(client: TestClient, db: Session) -> None:
    payload = {"email": "ada@cindra.dev", "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    wrong = {"email": payload["email"], "password": "wrong-password"}
    for _ in range(3):
        client.post("/auth/login", json=wrong)

    response = client.post("/auth/login", json=payload)
    assert response.status_code == 200

    user = db.scalar(select(User).where(User.email == payload["email"]))
    assert user.failed_login_attempts == 0
    assert user.locked_until is None


def test_me_without_token_rejected(client: TestClient) -> None:
    response = client.get("/auth/me")
    assert response.status_code == 401


def _google_claims(**overrides: str) -> dict[str, str]:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "test-client-id.apps.googleusercontent.com",
        "email": "google-user@gmail.com",
        "email_verified": "true",
        "sub": "1234567890",
    }
    claims.update(overrides)
    return claims


def test_google_login_returns_503_when_not_configured(client: TestClient) -> None:
    response = client.post("/auth/google", json={"id_token": "whatever"})
    assert response.status_code == 503


def test_google_login_creates_user_and_returns_usable_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        get_settings(), "google_client_id", "test-client-id.apps.googleusercontent.com"
    )
    with patch("app.routers.auth.verify_google_id_token", return_value=_google_claims()):
        response = client.post("/auth/google", json={"id_token": "valid-google-token"})
    assert response.status_code == 200
    token = response.json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "google-user@gmail.com"


def test_google_login_creates_subscription_like_register(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        get_settings(), "google_client_id", "test-client-id.apps.googleusercontent.com"
    )
    with patch("app.routers.auth.verify_google_id_token", return_value=_google_claims()):
        client.post("/auth/google", json={"id_token": "valid-google-token"})
    user = db.scalar(select(User).where(User.email == "google-user@gmail.com"))
    assert user is not None
    assert user.hashed_password is None
    subscription = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    assert subscription is not None


def test_concurrent_google_login_for_a_new_email_does_not_500(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same class of race as registration, above -- two tabs completing
    # "Sign in with Google" for the same brand-new email at once. Both
    # are legitimate logins by the same person, so the loser of the
    # create-race should just log into the account the winner created,
    # not error.
    monkeypatch.setattr(
        get_settings(), "google_client_id", "test-client-id.apps.googleusercontent.com"
    )
    results: list[int] = []
    barrier = threading.Barrier(2)

    def attempt() -> None:
        barrier.wait(timeout=5)
        response = client.post("/auth/google", json={"id_token": "valid-google-token"})
        results.append(response.status_code)

    with patch("app.routers.auth.verify_google_id_token", return_value=_google_claims()):
        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

    assert results == [200, 200]
    users = list(db.scalars(select(User).where(User.email == "google-user@gmail.com")))
    assert len(users) == 1
    assert len(list(db.scalars(select(Subscription).where(Subscription.user_id == users[0].id)))) == 1


def test_google_login_reuses_existing_email_account(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        get_settings(), "google_client_id", "test-client-id.apps.googleusercontent.com"
    )
    client.post(
        "/auth/register",
        json={"email": "google-user@gmail.com", "password": "supersecret1"},
    )
    with patch("app.routers.auth.verify_google_id_token", return_value=_google_claims()):
        response = client.post("/auth/google", json={"id_token": "valid-google-token"})
    assert response.status_code == 200
    users = db.scalars(select(User).where(User.email == "google-user@gmail.com")).all()
    assert len(users) == 1
    # CIN-140: the pre-existing password is dropped -- see the
    # pre-hijacking defence in login_with_google.
    assert users[0].hashed_password is None


def test_google_login_rejects_invalid_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        get_settings(), "google_client_id", "test-client-id.apps.googleusercontent.com"
    )
    with patch(
        "app.routers.auth.verify_google_id_token",
        side_effect=GoogleAuthError("Недействительный или просроченный токен Google"),
    ):
        response = client.post("/auth/google", json={"id_token": "bad-token"})
    assert response.status_code == 401


def test_password_login_on_google_only_account_names_the_fix(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        get_settings(), "google_client_id", "test-client-id.apps.googleusercontent.com"
    )
    with patch("app.routers.auth.verify_google_id_token", return_value=_google_claims()):
        client.post("/auth/google", json={"id_token": "valid-google-token"})
    response = client.post(
        "/auth/login",
        json={"email": "google-user@gmail.com", "password": "any-password"},
    )
    assert response.status_code == 401
    assert "Google" in response.json()["detail"]


def test_google_login_disables_a_password_set_by_someone_else(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CIN-140: account pre-hijacking defence.

    Registration doesn't verify the address, so an attacker can claim
    somebody else's email with a password first. When the real owner
    signs in with Google, that password must stop working -- otherwise
    the attacker keeps a way into an account that owns connected social
    accounts.
    """
    monkeypatch.setattr(
        get_settings(), "google_client_id", "test-client-id.apps.googleusercontent.com"
    )
    attacker_credentials = {"email": "google-user@gmail.com", "password": "attacker-pass1"}
    client.post("/auth/register", json=attacker_credentials)
    assert client.post("/auth/login", json=attacker_credentials).status_code == 200

    with patch("app.routers.auth.verify_google_id_token", return_value=_google_claims()):
        google_login = client.post("/auth/google", json={"id_token": "valid-google-token"})
    assert google_login.status_code == 200

    # the attacker's password no longer opens the account
    locked_out = client.post("/auth/login", json=attacker_credentials)
    assert locked_out.status_code == 401
    assert "Google" in locked_out.json()["detail"]

    # and the real owner keeps access through Google
    with patch("app.routers.auth.verify_google_id_token", return_value=_google_claims()):
        again = client.post("/auth/google", json={"id_token": "valid-google-token"})
    assert again.status_code == 200
    assert len(db.scalars(select(User).where(User.email == "google-user@gmail.com")).all()) == 1
