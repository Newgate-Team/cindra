from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import EmailVerificationToken, User


def _google_claims(**overrides: str) -> dict[str, str]:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "test-client-id.apps.googleusercontent.com",
        "email": "ada@cindra.dev",
        "email_verified": "true",
        "sub": "1234567890",
    }
    claims.update(overrides)
    return claims


@pytest.fixture(autouse=True)
def _smtp_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.test.invalid")
    monkeypatch.setattr(get_settings(), "frontend_base_url", "https://app.cindra.test")


@pytest.fixture
def sent_emails(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    # Patched at the call site (app.scheduler.tasks), not app.email --
    # see test_password_reset.py for why.
    sent: list[dict] = []

    def fake_send_email(to: str, subject: str, body: str) -> None:
        sent.append({"to": to, "subject": subject, "body": body})

    monkeypatch.setattr("app.scheduler.tasks.send_email", fake_send_email)
    return sent


def _register_and_login(
    client: TestClient, email: str = "ada@cindra.dev", password: str = "supersecret1"
) -> str:
    payload = {"email": email, "password": password}
    client.post("/auth/register", json=payload)
    return client.post("/auth/login", json=payload).json()["access_token"]


def _extract_token(body: str) -> str:
    return body.split("token=")[1].split()[0].strip()


def test_register_sends_a_verification_email(
    client: TestClient, db: Session, sent_emails: list[dict]
) -> None:
    client.post("/auth/register", json={"email": "ada@cindra.dev", "password": "supersecret1"})

    assert len(sent_emails) == 1
    assert sent_emails[0]["to"] == "ada@cindra.dev"
    assert "https://app.cindra.test/verify-email?token=" in sent_emails[0]["body"]

    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    assert user.email_verified is False


def test_register_succeeds_even_when_sending_the_verification_email_blows_up(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Unlike the "SMTP not configured" case above (a clean early return,
    # never touches the try/except), this drives an actual exception out
    # of _send_verification_email to prove the swallow in register()
    # itself is doing something -- a raising .delay() (e.g. Redis down)
    # must not turn into a failed registration either.
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr("app.routers.auth.send_email_task.delay", boom)

    response = client.post(
        "/auth/register", json={"email": "ada@cindra.dev", "password": "supersecret1"}
    )
    assert response.status_code == 201

    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    assert user is not None
    assert user.email_verified is False


def test_register_succeeds_even_when_smtp_is_not_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The verification email is best-effort -- registration itself must
    # never fail just because SMTP isn't set up.
    monkeypatch.setattr(get_settings(), "smtp_host", "")
    response = client.post(
        "/auth/register", json={"email": "ada@cindra.dev", "password": "supersecret1"}
    )
    assert response.status_code == 201
    assert response.json()["email_verified"] is False


def test_me_reports_email_verified_field(client: TestClient) -> None:
    token = _register_and_login(client)
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.json()["email_verified"] is False


def test_google_signup_is_verified_immediately(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        get_settings(), "google_client_id", "test-client-id.apps.googleusercontent.com"
    )
    with patch("app.routers.auth.verify_google_id_token", return_value=_google_claims()):
        response = client.post("/auth/google", json={"id_token": "irrelevant"})
    assert response.status_code == 200

    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    assert user.email_verified is True


def test_google_link_of_existing_password_account_marks_verified(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_and_login(client)
    monkeypatch.setattr(
        get_settings(), "google_client_id", "test-client-id.apps.googleusercontent.com"
    )
    with patch("app.routers.auth.verify_google_id_token", return_value=_google_claims()):
        response = client.post("/auth/google", json={"id_token": "irrelevant"})
    assert response.status_code == 200

    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    assert user.email_verified is True


def test_request_returns_503_when_smtp_not_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _register_and_login(client)
    monkeypatch.setattr(get_settings(), "smtp_host", "")
    response = client.post(
        "/auth/verify-email/request", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 503


def test_request_requires_authentication(client: TestClient) -> None:
    response = client.post("/auth/verify-email/request")
    assert response.status_code == 401


def test_request_resends_a_verification_link(
    client: TestClient, db: Session, sent_emails: list[dict]
) -> None:
    token = _register_and_login(client)
    sent_emails.clear()  # only care about emails sent from here on

    response = client.post(
        "/auth/verify-email/request", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert len(sent_emails) == 1
    assert "https://app.cindra.test/verify-email?token=" in sent_emails[0]["body"]

    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    tokens = db.scalars(
        select(EmailVerificationToken).where(EmailVerificationToken.user_id == user.id)
    ).all()
    # One from registration, one from this resend -- the registration
    # token is invalidated by the resend, same as password-reset.
    assert len(tokens) == 2
    assert sum(1 for t in tokens if t.used_at is None) == 1


def test_request_is_a_no_op_once_already_verified(
    client: TestClient, db: Session, sent_emails: list[dict]
) -> None:
    token = _register_and_login(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    user.email_verified = True
    db.commit()
    sent_emails.clear()

    response = client.post(
        "/auth/verify-email/request", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert sent_emails == []


def test_confirm_with_valid_token_marks_email_verified(
    client: TestClient, db: Session, sent_emails: list[dict]
) -> None:
    _register_and_login(client)
    verify_token = _extract_token(sent_emails[0]["body"])

    response = client.post("/auth/verify-email/confirm", json={"token": verify_token})
    assert response.status_code == 204

    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    assert user.email_verified is True


def test_confirm_with_same_token_twice_is_rejected_the_second_time(
    client: TestClient, sent_emails: list[dict]
) -> None:
    _register_and_login(client)
    verify_token = _extract_token(sent_emails[0]["body"])

    first = client.post("/auth/verify-email/confirm", json={"token": verify_token})
    assert first.status_code == 204

    second = client.post("/auth/verify-email/confirm", json={"token": verify_token})
    assert second.status_code == 400


def test_confirm_with_unknown_token_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/auth/verify-email/confirm", json={"token": "not-a-real-token"}
    )
    assert response.status_code == 400


def test_confirm_with_expired_token_is_rejected(
    client: TestClient, db: Session, sent_emails: list[dict]
) -> None:
    _register_and_login(client)
    verify_token = _extract_token(sent_emails[0]["body"])

    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    verification_token = db.scalar(
        select(EmailVerificationToken).where(EmailVerificationToken.user_id == user.id)
    )
    verification_token.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()

    response = client.post("/auth/verify-email/confirm", json={"token": verify_token})
    assert response.status_code == 400
