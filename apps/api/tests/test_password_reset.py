from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import PasswordResetToken, Subscription, User


@pytest.fixture(autouse=True)
def _smtp_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    # Every test in this file needs the 503 gate open -- the one test
    # that specifically wants the gate closed (below) undoes this
    # itself rather than skip the fixture, so it's still exercising the
    # same code path as every other test here.
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.test.invalid")
    monkeypatch.setattr(get_settings(), "frontend_base_url", "https://app.cindra.test")


@pytest.fixture
def sent_emails(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    # send_email_task.delay() runs eagerly in tests (task_always_eager,
    # conftest.py) -- patched at the call site (app.scheduler.tasks),
    # not app.email itself, since that's where the name is actually
    # looked up from from a `from ... import` binding.
    sent: list[dict] = []

    def fake_send_email(to: str, subject: str, body: str) -> None:
        sent.append({"to": to, "subject": subject, "body": body})

    monkeypatch.setattr("app.scheduler.tasks.send_email", fake_send_email)
    return sent


def _register(client: TestClient, email: str = "ada@cindra.dev", password: str = "supersecret1") -> None:
    client.post("/auth/register", json={"email": email, "password": password})


def test_request_returns_503_when_smtp_not_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "smtp_host", "")
    response = client.post("/auth/password-reset/request", json={"email": "ada@cindra.dev"})
    assert response.status_code == 503


def test_request_for_unknown_email_still_returns_generic_200(
    client: TestClient, sent_emails: list[dict]
) -> None:
    # No user enumeration -- an unregistered email gets the exact same
    # response as a registered one.
    response = client.post(
        "/auth/password-reset/request", json={"email": "nobody@cindra.dev"}
    )
    assert response.status_code == 200
    assert sent_emails == []


def test_request_for_registered_email_sends_a_reset_link(
    client: TestClient, db: Session, sent_emails: list[dict]
) -> None:
    _register(client)
    sent_emails.clear()  # only care about emails sent from here on
    response = client.post("/auth/password-reset/request", json={"email": "ada@cindra.dev"})
    assert response.status_code == 200

    assert len(sent_emails) == 1
    assert sent_emails[0]["to"] == "ada@cindra.dev"
    assert "https://app.cindra.test/reset-password?token=" in sent_emails[0]["body"]

    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    tokens = db.scalars(
        select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
    ).all()
    assert len(tokens) == 1
    assert tokens[0].used_at is None


def test_request_for_google_only_account_names_the_fix_instead(
    client: TestClient, db: Session, sent_emails: list[dict]
) -> None:
    user = User(email="google-only@cindra.dev", hashed_password=None)
    db.add(user)
    db.flush()
    db.add(Subscription(user_id=user.id))
    db.commit()

    response = client.post(
        "/auth/password-reset/request", json={"email": "google-only@cindra.dev"}
    )
    assert response.status_code == 200
    assert len(sent_emails) == 1
    assert "Google" in sent_emails[0]["body"]

    # No reset token was ever created for an account with no password.
    tokens = db.scalars(
        select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
    ).all()
    assert tokens == []


def test_second_request_invalidates_the_first_token(
    client: TestClient, db: Session, sent_emails: list[dict]
) -> None:
    _register(client)
    sent_emails.clear()  # only care about emails sent from here on
    client.post("/auth/password-reset/request", json={"email": "ada@cindra.dev"})
    client.post("/auth/password-reset/request", json={"email": "ada@cindra.dev"})

    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    tokens = db.scalars(
        select(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id)
        .order_by(PasswordResetToken.created_at)
    ).all()
    assert len(tokens) == 2
    assert tokens[0].used_at is not None  # invalidated by the second request
    assert tokens[1].used_at is None


def _extract_token(body: str) -> str:
    return body.split("token=")[1].split()[0].strip()


def test_confirm_with_valid_token_changes_password_and_new_password_logs_in(
    client: TestClient, sent_emails: list[dict]
) -> None:
    _register(client)
    sent_emails.clear()  # only care about emails sent from here on
    client.post("/auth/password-reset/request", json={"email": "ada@cindra.dev"})
    token = _extract_token(sent_emails[0]["body"])

    response = client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "brandnewpass2"},
    )
    assert response.status_code == 204

    old_login = client.post(
        "/auth/login", json={"email": "ada@cindra.dev", "password": "supersecret1"}
    )
    assert old_login.status_code == 401

    new_login = client.post(
        "/auth/login", json={"email": "ada@cindra.dev", "password": "brandnewpass2"}
    )
    assert new_login.status_code == 200


def test_confirm_with_same_token_twice_is_rejected_the_second_time(
    client: TestClient, sent_emails: list[dict]
) -> None:
    _register(client)
    sent_emails.clear()  # only care about emails sent from here on
    client.post("/auth/password-reset/request", json={"email": "ada@cindra.dev"})
    token = _extract_token(sent_emails[0]["body"])

    first = client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "brandnewpass2"},
    )
    assert first.status_code == 204

    second = client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "yetanotherpass3"},
    )
    assert second.status_code == 400


def test_confirm_with_unknown_token_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/auth/password-reset/confirm",
        json={"token": "not-a-real-token", "new_password": "brandnewpass2"},
    )
    assert response.status_code == 400


def test_confirm_with_expired_token_is_rejected(
    client: TestClient, db: Session, sent_emails: list[dict]
) -> None:
    _register(client)
    sent_emails.clear()  # only care about emails sent from here on
    client.post("/auth/password-reset/request", json={"email": "ada@cindra.dev"})
    token = _extract_token(sent_emails[0]["body"])

    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    reset_token = db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
    )
    reset_token.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()

    response = client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "brandnewpass2"},
    )
    assert response.status_code == 400


def test_confirm_clears_login_lockout(
    client: TestClient, db: Session, sent_emails: list[dict]
) -> None:
    _register(client)
    sent_emails.clear()  # only care about emails sent from here on
    wrong = {"email": "ada@cindra.dev", "password": "wrong-password"}
    for _ in range(5):
        client.post("/auth/login", json=wrong)
    locked = client.post("/auth/login", json=wrong)
    assert locked.status_code == 429

    client.post("/auth/password-reset/request", json={"email": "ada@cindra.dev"})
    token = _extract_token(sent_emails[-1]["body"])
    client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "brandnewpass2"},
    )

    response = client.post(
        "/auth/login", json={"email": "ada@cindra.dev", "password": "brandnewpass2"}
    )
    assert response.status_code == 200
