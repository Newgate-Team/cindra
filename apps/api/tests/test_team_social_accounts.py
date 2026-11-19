import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SocialAccount, SocialPlatform, Subscription, User
from app.social_accounts import upsert_social_account
from app.teams import visible_user_ids


def _register_and_login(
    client: TestClient, email: str, role: str = "agency"
) -> str:
    payload = {"email": email, "password": "supersecret1", "role": role}
    client.post("/auth/register", json=payload)
    return client.post(
        "/auth/login", json={"email": email, "password": "supersecret1"}
    ).json()["access_token"]


def _make_team(
    client: TestClient, db: Session, owner_email: str, member_email: str
) -> tuple[User, User]:
    """Owner (agency) creates a team, member joins directly via DB --
    same shortcut test_team.py uses for tests that aren't about the
    invite/accept mechanics themselves."""
    owner_token = _register_and_login(client, owner_email)
    team_id = client.post(
        "/team", json={"name": "Команда"}, headers={"Authorization": f"Bearer {owner_token}"}
    ).json()["id"]

    _register_and_login(client, member_email, role="solo")
    owner = db.scalar(select(User).where(User.email == owner_email))
    member = db.scalar(select(User).where(User.email == member_email))
    member.team_id = team_id
    db.commit()
    db.refresh(owner)
    db.refresh(member)
    return owner, member


def _login(client: TestClient, email: str) -> dict[str, str]:
    token = client.post(
        "/auth/login", json={"email": email, "password": "supersecret1"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_list_shows_teammates_connected_accounts(client: TestClient, db: Session) -> None:
    owner, _member = _make_team(client, db, "owner@cindra.dev", "member@cindra.dev")
    upsert_social_account(db, owner, SocialPlatform.telegram, "chat-1", access_token="t")

    response = client.get("/social-accounts", headers=_login(client, "member@cindra.dev"))
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["external_account_id"] == "chat-1"


def test_list_excludes_other_teams_accounts(client: TestClient, db: Session) -> None:
    _owner_a, _member_a = _make_team(client, db, "owner-a@cindra.dev", "member-a@cindra.dev")
    owner_b, _member_b = _make_team(client, db, "owner-b@cindra.dev", "member-b@cindra.dev")
    upsert_social_account(db, owner_b, SocialPlatform.telegram, "chat-b", access_token="t")

    response = client.get("/social-accounts", headers=_login(client, "member-a@cindra.dev"))
    assert response.status_code == 200
    assert response.json() == []


def test_solo_user_still_sees_only_their_own_accounts(db: Session) -> None:
    solo = User(email="solo@cindra.dev", hashed_password="x")
    other = User(email="other-solo@cindra.dev", hashed_password="x")
    db.add_all([solo, other])
    db.flush()
    db.add_all([Subscription(user_id=solo.id), Subscription(user_id=other.id)])
    db.commit()

    assert visible_user_ids(db, solo) == [solo.id]


def test_reconnecting_a_teammates_channel_refreshes_the_same_row(
    client: TestClient, db: Session
) -> None:
    owner, member = _make_team(client, db, "owner2@cindra.dev", "member2@cindra.dev")
    first = upsert_social_account(
        db, owner, SocialPlatform.telegram, "shared-chat", access_token="original-token"
    )

    second = upsert_social_account(
        db, member, SocialPlatform.telegram, "shared-chat", access_token="refreshed-token"
    )

    assert second.id == first.id
    assert second.user_id == owner.id  # attribution doesn't move to whoever refreshed it

    rows = db.scalars(
        select(SocialAccount).where(
            SocialAccount.platform == SocialPlatform.telegram,
            SocialAccount.external_account_id == "shared-chat",
        )
    ).all()
    assert len(rows) == 1  # not duplicated


def test_connected_account_limit_is_pooled_across_the_team(
    client: TestClient, db: Session
) -> None:
    # Free tier: 1 connected account (app/plans.py). Owner uses it up;
    # the member (same team, same shared quota) must not get their own
    # separate slot.
    owner, member = _make_team(client, db, "owner3@cindra.dev", "member3@cindra.dev")
    upsert_social_account(db, owner, SocialPlatform.telegram, "owner-chat", access_token="t")

    with pytest.raises(HTTPException) as exc_info:
        upsert_social_account(
            db, member, SocialPlatform.tiktok, "member-chat", access_token="t2"
        )
    assert exc_info.value.status_code == 402
    assert (
        db.query(SocialAccount)
        .filter(SocialAccount.user_id.in_([owner.id, member.id]))
        .count()
        == 1
    )


def test_teammate_can_disconnect_an_account_connected_by_the_owner(
    client: TestClient, db: Session
) -> None:
    owner, _member = _make_team(client, db, "owner4@cindra.dev", "member4@cindra.dev")
    account = upsert_social_account(
        db, owner, SocialPlatform.telegram, "chat-4", access_token="t"
    )

    response = client.delete(
        f"/social-accounts/{account.id}", headers=_login(client, "member4@cindra.dev")
    )
    assert response.status_code == 204


def test_outsider_cannot_disconnect_another_teams_account(
    client: TestClient, db: Session
) -> None:
    owner_a, _member_a = _make_team(client, db, "owner5a@cindra.dev", "member5a@cindra.dev")
    _owner_b, _member_b = _make_team(client, db, "owner5b@cindra.dev", "member5b@cindra.dev")
    account = upsert_social_account(
        db, owner_a, SocialPlatform.telegram, "chat-5a", access_token="t"
    )

    response = client.delete(
        f"/social-accounts/{account.id}", headers=_login(client, "member5b@cindra.dev")
    )
    assert response.status_code == 404
