import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import TeamInvite, User
from app.teams import visible_user_ids


@pytest.fixture(autouse=True)
def _smtp_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.test.invalid")
    monkeypatch.setattr(get_settings(), "frontend_base_url", "https://app.cindra.test")


@pytest.fixture
def sent_emails(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    sent: list[dict] = []

    def fake_send_email(to: str, subject: str, body: str) -> None:
        sent.append({"to": to, "subject": subject, "body": body})

    monkeypatch.setattr("app.scheduler.tasks.send_email", fake_send_email)
    return sent


def _register_and_login(
    client: TestClient, email: str = "owner@cindra.dev", role: str = "agency"
) -> dict[str, str]:
    payload = {"email": email, "password": "supersecret1", "role": role}
    client.post("/auth/register", json=payload)
    token = client.post(
        "/auth/login", json={"email": email, "password": "supersecret1"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _extract_token(body: str) -> str:
    return body.split("token=")[1].split()[0].strip()


def _extract_invite_token(sent_emails: list[dict]) -> str:
    # _register_and_login's own registration also sends a verification
    # email (PR #172) into this same list -- pick the team-invite one
    # specifically rather than assuming a fixed index.
    for entry in reversed(sent_emails):
        if "team/accept?token=" in entry["body"]:
            return _extract_token(entry["body"])
    raise AssertionError("no team invite email found in sent_emails")


def test_create_requires_auth(client: TestClient) -> None:
    response = client.post("/team", json={"name": "Моё агентство"})
    assert response.status_code == 401


def test_create_requires_agency_role(client: TestClient) -> None:
    headers = _register_and_login(client, role="solo")
    response = client.post("/team", json={"name": "Моё агентство"}, headers=headers)
    assert response.status_code == 403


def test_create_succeeds_for_agency_role(client: TestClient, db: Session) -> None:
    headers = _register_and_login(client)
    response = client.post("/team", json={"name": "Моё агентство"}, headers=headers)
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Моё агентство"
    assert len(body["members"]) == 1
    assert body["members"][0]["email"] == "owner@cindra.dev"
    assert body["members"][0]["is_owner"] is True

    owner = db.scalar(select(User).where(User.email == "owner@cindra.dev"))
    assert owner.team_id == uuid.UUID(body["id"])


def test_create_rejects_when_already_in_a_team(client: TestClient) -> None:
    headers = _register_and_login(client)
    client.post("/team", json={"name": "Первая"}, headers=headers)
    response = client.post("/team", json={"name": "Вторая"}, headers=headers)
    assert response.status_code == 400


def test_get_requires_membership(client: TestClient) -> None:
    headers = _register_and_login(client)
    response = client.get("/team", headers=headers)
    assert response.status_code == 404


def test_get_returns_team_with_members(client: TestClient) -> None:
    headers = _register_and_login(client)
    created = client.post("/team", json={"name": "Команда"}, headers=headers).json()

    response = client.get("/team", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_invites_require_owner(client: TestClient, db: Session) -> None:
    owner_headers = _register_and_login(client, "owner2@cindra.dev")
    team_id = client.post("/team", json={"name": "Команда"}, headers=owner_headers).json()["id"]

    # A second member joins via direct DB manipulation (simplest way to
    # get a non-owner member in place without going through the full
    # invite/accept round trip for this particular test).
    member_headers = _register_and_login(client, "member@cindra.dev")
    member = db.scalar(select(User).where(User.email == "member@cindra.dev"))
    member.team_id = team_id
    db.commit()

    response = client.post(
        "/team/invites", json={"email": "outsider@cindra.dev"}, headers=member_headers
    )
    assert response.status_code == 403


def test_invites_require_smtp_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _register_and_login(client)
    client.post("/team", json={"name": "Команда"}, headers=headers)
    monkeypatch.setattr(get_settings(), "smtp_host", "")

    response = client.post(
        "/team/invites", json={"email": "invitee@cindra.dev"}, headers=headers
    )
    assert response.status_code == 503


def test_invites_sends_email_and_creates_invite(
    client: TestClient, db: Session, sent_emails: list[dict]
) -> None:
    headers = _register_and_login(client)
    client.post("/team", json={"name": "Команда"}, headers=headers)

    sent_emails.clear()  # only care about emails sent from here on
    response = client.post(
        "/team/invites", json={"email": "invitee@cindra.dev"}, headers=headers
    )
    assert response.status_code == 201
    assert response.json()["email"] == "invitee@cindra.dev"

    assert len(sent_emails) == 1
    assert sent_emails[0]["to"] == "invitee@cindra.dev"
    assert "https://app.cindra.test/team/accept?token=" in sent_emails[0]["body"]

    invite = db.scalar(select(TeamInvite).where(TeamInvite.email == "invitee@cindra.dev"))
    assert invite is not None
    assert invite.accepted_at is None


def test_invites_reject_already_a_member(client: TestClient) -> None:
    headers = _register_and_login(client)
    client.post("/team", json={"name": "Команда"}, headers=headers)

    response = client.post(
        "/team/invites", json={"email": "owner@cindra.dev"}, headers=headers
    )
    assert response.status_code == 400


def test_second_invite_invalidates_the_first(
    client: TestClient, db: Session, sent_emails: list[dict]
) -> None:
    headers = _register_and_login(client)
    client.post("/team", json={"name": "Команда"}, headers=headers)
    client.post("/team/invites", json={"email": "invitee@cindra.dev"}, headers=headers)
    client.post("/team/invites", json={"email": "invitee@cindra.dev"}, headers=headers)

    invites = db.scalars(
        select(TeamInvite)
        .where(TeamInvite.email == "invitee@cindra.dev")
        .order_by(TeamInvite.created_at)
    ).all()
    assert len(invites) == 2
    assert invites[0].accepted_at is not None  # invalidated by the second invite
    assert invites[1].accepted_at is None


def test_accept_requires_matching_email(client: TestClient, sent_emails: list[dict]) -> None:
    owner_headers = _register_and_login(client)
    client.post("/team", json={"name": "Команда"}, headers=owner_headers)
    client.post("/team/invites", json={"email": "invitee@cindra.dev"}, headers=owner_headers)
    token = _extract_invite_token(sent_emails)

    wrong_person_headers = _register_and_login(client, "someone-else@cindra.dev", role="solo")
    response = client.post(
        "/team/invites/accept", json={"token": token}, headers=wrong_person_headers
    )
    assert response.status_code == 400


def test_accept_requires_not_already_in_a_team(
    client: TestClient, sent_emails: list[dict]
) -> None:
    owner_headers = _register_and_login(client)
    client.post("/team", json={"name": "Команда"}, headers=owner_headers)
    client.post("/team/invites", json={"email": "invitee@cindra.dev"}, headers=owner_headers)
    token = _extract_invite_token(sent_emails)

    invitee_headers = _register_and_login(client, "invitee@cindra.dev")
    client.post("/team", json={"name": "Своя команда"}, headers=invitee_headers)

    response = client.post(
        "/team/invites/accept", json={"token": token}, headers=invitee_headers
    )
    assert response.status_code == 400


def test_accept_rejects_expired_token(
    client: TestClient, db: Session, sent_emails: list[dict]
) -> None:
    owner_headers = _register_and_login(client)
    client.post("/team", json={"name": "Команда"}, headers=owner_headers)
    client.post("/team/invites", json={"email": "invitee@cindra.dev"}, headers=owner_headers)
    token = _extract_invite_token(sent_emails)

    invite = db.scalar(select(TeamInvite).where(TeamInvite.email == "invitee@cindra.dev"))
    invite.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()

    invitee_headers = _register_and_login(client, "invitee@cindra.dev", role="solo")
    response = client.post(
        "/team/invites/accept", json={"token": token}, headers=invitee_headers
    )
    assert response.status_code == 400


def test_accept_with_same_token_twice_is_rejected_the_second_time(
    client: TestClient, sent_emails: list[dict]
) -> None:
    owner_headers = _register_and_login(client)
    client.post("/team", json={"name": "Команда"}, headers=owner_headers)
    client.post("/team/invites", json={"email": "invitee@cindra.dev"}, headers=owner_headers)
    token = _extract_invite_token(sent_emails)

    invitee_headers = _register_and_login(client, "invitee@cindra.dev", role="solo")
    first = client.post(
        "/team/invites/accept", json={"token": token}, headers=invitee_headers
    )
    assert first.status_code == 200

    other_headers = _register_and_login(client, "another@cindra.dev", role="solo")
    second = client.post(
        "/team/invites/accept", json={"token": token}, headers=other_headers
    )
    assert second.status_code == 400


def test_accept_joins_the_team(client: TestClient, db: Session, sent_emails: list[dict]) -> None:
    owner_headers = _register_and_login(client)
    team_id = client.post("/team", json={"name": "Команда"}, headers=owner_headers).json()["id"]
    client.post("/team/invites", json={"email": "invitee@cindra.dev"}, headers=owner_headers)
    token = _extract_invite_token(sent_emails)

    invitee_headers = _register_and_login(client, "invitee@cindra.dev", role="solo")
    response = client.post(
        "/team/invites/accept", json={"token": token}, headers=invitee_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == team_id
    assert len(body["members"]) == 2

    invitee = db.scalar(select(User).where(User.email == "invitee@cindra.dev"))
    assert str(invitee.team_id) == team_id


def test_remove_member_requires_owner(client: TestClient, db: Session) -> None:
    owner_headers = _register_and_login(client)
    team_id = client.post("/team", json={"name": "Команда"}, headers=owner_headers).json()["id"]

    member_headers = _register_and_login(client, "member@cindra.dev")
    member = db.scalar(select(User).where(User.email == "member@cindra.dev"))
    member.team_id = team_id
    db.commit()

    response = client.delete(f"/team/members/{member.id}", headers=member_headers)
    assert response.status_code == 403


def test_remove_member_cannot_remove_owner(client: TestClient, db: Session) -> None:
    owner_headers = _register_and_login(client)
    client.post("/team", json={"name": "Команда"}, headers=owner_headers)
    owner = db.scalar(select(User).where(User.email == "owner@cindra.dev"))

    response = client.delete(f"/team/members/{owner.id}", headers=owner_headers)
    assert response.status_code == 400


def test_remove_member_succeeds(client: TestClient, db: Session) -> None:
    owner_headers = _register_and_login(client)
    team_id = client.post("/team", json={"name": "Команда"}, headers=owner_headers).json()["id"]

    _register_and_login(client, "member@cindra.dev")
    member = db.scalar(select(User).where(User.email == "member@cindra.dev"))
    member.team_id = team_id
    db.commit()

    response = client.delete(f"/team/members/{member.id}", headers=owner_headers)
    assert response.status_code == 204

    db.refresh(member)
    assert member.team_id is None


def test_visible_user_ids_solo_user_sees_only_self(db: Session, user: User) -> None:
    assert visible_user_ids(db, user) == [user.id]


def test_visible_user_ids_team_member_sees_whole_team(client: TestClient, db: Session) -> None:
    owner_headers = _register_and_login(client)
    team_id = client.post("/team", json={"name": "Команда"}, headers=owner_headers).json()["id"]

    _register_and_login(client, "member@cindra.dev")
    member = db.scalar(select(User).where(User.email == "member@cindra.dev"))
    member.team_id = team_id
    db.commit()

    owner = db.scalar(select(User).where(User.email == "owner@cindra.dev"))
    ids = set(visible_user_ids(db, owner))
    assert ids == {owner.id, member.id}
