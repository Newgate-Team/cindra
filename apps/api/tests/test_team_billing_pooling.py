from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content_pipeline import registry
from app.content_pipeline.registry import register_generator
from app.models import (
    GenerationContentType,
    SocialPlatform,
    Subscription,
    SubscriptionTier,
    UsageEvent,
    UsageEventType,
    User,
)
from app.social_accounts import upsert_social_account
from app.teams import billing_owner


@pytest.fixture(autouse=True)
def _fake_text_generator():
    previous = dict(registry._REGISTRY)
    register_generator(
        GenerationContentType.text, lambda payload: {"text": f"пост про {payload['topic']}"}
    )
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(previous)


def _register_and_login(client: TestClient, email: str, role: str = "agency") -> str:
    client.post(
        "/auth/register", json={"email": email, "password": "supersecret1", "role": role}
    )
    return client.post(
        "/auth/login", json={"email": email, "password": "supersecret1"}
    ).json()["access_token"]


def _login(client: TestClient, email: str) -> dict[str, str]:
    token = client.post(
        "/auth/login", json={"email": email, "password": "supersecret1"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _make_team(
    client: TestClient, db: Session, owner_email: str, member_email: str
) -> tuple[User, User, dict[str, str]]:
    owner_token = _register_and_login(client, owner_email)
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    team_id = client.post("/team", json={"name": "Команда"}, headers=owner_headers).json()["id"]

    _register_and_login(client, member_email, role="solo")
    owner = db.scalar(select(User).where(User.email == owner_email))
    member = db.scalar(select(User).where(User.email == member_email))
    member.team_id = team_id
    db.commit()
    db.refresh(owner)
    db.refresh(member)
    return owner, member, owner_headers


# ---- app/teams.py::billing_owner ----


def test_billing_owner_solo_user_is_self(db: Session, user: User) -> None:
    assert billing_owner(db, user).id == user.id


def test_billing_owner_team_member_resolves_to_the_owner(
    client: TestClient, db: Session
) -> None:
    owner, member, _ = _make_team(client, db, "bo-owner@cindra.dev", "bo-member@cindra.dev")
    assert billing_owner(db, member).id == owner.id
    assert billing_owner(db, owner).id == owner.id


# ---- Generation quota pooling (app/usage.py via /content/generate) ----


def test_generation_quota_is_pooled_across_the_team(client: TestClient, db: Session) -> None:
    # Free tier: 20 text generations/month (app/plans.py). Burn 19 as
    # the owner, then the member's 2 more must not both fit -- they're
    # drawing on the same pool, not their own separate 20.
    owner, _member, _owner_headers = _make_team(
        client, db, "gq-owner@cindra.dev", "gq-member@cindra.dev"
    )
    db.add_all(
        [
            UsageEvent(
                user_id=owner.id,
                event_type=UsageEventType.generation,
                content_type=GenerationContentType.text,
            )
            for _ in range(19)
        ]
    )
    db.commit()

    member_headers = _login(client, "gq-member@cindra.dev")
    first = client.post("/content/generate", json={"topic": "тест 1"}, headers=member_headers)
    assert first.status_code == 202  # 20th -- still fits

    second = client.post("/content/generate", json={"topic": "тест 2"}, headers=member_headers)
    assert second.status_code == 402  # 21st -- pool exhausted


def test_generation_quota_resolves_the_owners_tier_not_the_members_own(
    client: TestClient, db: Session
) -> None:
    # Member's own personal Subscription stays free tier; the team
    # owner's is upgraded to business (600 text/month). The member
    # must be governed by the team's tier, not their own free one.
    owner, member, _owner_headers = _make_team(
        client, db, "tr-owner@cindra.dev", "tr-member@cindra.dev"
    )
    owner_subscription = db.scalar(select(Subscription).where(Subscription.user_id == owner.id))
    owner_subscription.tier = SubscriptionTier.business
    db.commit()

    # Burn past the free tier's own 20-generation limit -- this would
    # 402 on the member's personal free subscription, but must not
    # under the team's business tier (600).
    db.add_all(
        [
            UsageEvent(
                user_id=member.id,
                event_type=UsageEventType.generation,
                content_type=GenerationContentType.text,
            )
            for _ in range(25)
        ]
    )
    db.commit()

    response = client.post(
        "/content/generate",
        json={"topic": "тест"},
        headers=_login(client, "tr-member@cindra.dev"),
    )
    assert response.status_code == 202


# ---- usage_summary / analytics "usage vs limits" ----


def test_analytics_usage_summary_reflects_the_pooled_team_quota(
    client: TestClient, db: Session
) -> None:
    owner, member, _owner_headers = _make_team(
        client, db, "us-owner@cindra.dev", "us-member@cindra.dev"
    )
    db.add_all(
        [
            UsageEvent(
                user_id=owner.id,
                event_type=UsageEventType.generation,
                content_type=GenerationContentType.text,
            ),
            UsageEvent(
                user_id=member.id,
                event_type=UsageEventType.generation,
                content_type=GenerationContentType.text,
            ),
        ]
    )
    db.commit()

    response = client.get(
        "/analytics/summary", headers=_login(client, "us-member@cindra.dev")
    )
    assert response.status_code == 200
    usage_by_kind = {
        (row["event_type"], row["content_type"]): row
        for row in response.json()["usage_this_period"]
    }
    text_row = usage_by_kind[("generation", "text")]
    assert text_row["used"] == 2  # both owner's and member's events counted
    assert text_row["limit"] == 20  # free tier -- the (unpromoted) owner's


# ---- GET /billing/subscription ----


def test_get_subscription_shows_the_team_owners_plan_to_a_member(
    client: TestClient, db: Session
) -> None:
    owner, _member, _owner_headers = _make_team(
        client, db, "sub-owner@cindra.dev", "sub-member@cindra.dev"
    )
    owner_subscription = db.scalar(select(Subscription).where(Subscription.user_id == owner.id))
    owner_subscription.tier = SubscriptionTier.pro
    db.commit()

    response = client.get(
        "/billing/subscription", headers=_login(client, "sub-member@cindra.dev")
    )
    assert response.status_code == 200
    assert response.json()["tier"] == "pro"  # not the member's own (still free)


# ---- POST /billing/paypal/confirm-subscription ----


@pytest.fixture
def _pro_plan_id(monkeypatch: pytest.MonkeyPatch) -> str:
    plan_id = "P-TEST-PRO"
    monkeypatch.setattr(get_settings(), "paypal_pro_plan_id", plan_id)
    return plan_id


def test_non_owner_member_cannot_change_the_teams_subscription(
    client: TestClient, db: Session, _pro_plan_id: str
) -> None:
    _owner, member, _owner_headers = _make_team(
        client, db, "pp-owner@cindra.dev", "pp-member@cindra.dev"
    )

    with patch(
        "app.billing_integrations.paypal.get_subscription",
        return_value={"custom_id": str(member.id), "plan_id": _pro_plan_id, "status": "ACTIVE"},
    ):
        response = client.post(
            "/billing/paypal/confirm-subscription",
            json={"subscription_id": "I-FAKE123"},
            headers=_login(client, "pp-member@cindra.dev"),
        )
    assert response.status_code == 403


def test_owner_can_still_change_the_teams_subscription(
    client: TestClient, db: Session, _pro_plan_id: str
) -> None:
    owner, _member, owner_headers = _make_team(
        client, db, "po-owner@cindra.dev", "po-member@cindra.dev"
    )

    with patch(
        "app.billing_integrations.paypal.get_subscription",
        return_value={"custom_id": str(owner.id), "plan_id": _pro_plan_id, "status": "ACTIVE"},
    ):
        response = client.post(
            "/billing/paypal/confirm-subscription",
            json={"subscription_id": "I-FAKE123"},
            headers=owner_headers,
        )
    assert response.status_code == 200
    assert response.json()["tier"] == "pro"


# ---- Connected-account limit tier resolution (completes stage 2's gap) ----


def test_connected_account_limit_resolves_the_owners_tier(
    client: TestClient, db: Session
) -> None:
    owner, member, _owner_headers = _make_team(
        client, db, "ca-owner@cindra.dev", "ca-member@cindra.dev"
    )
    owner_subscription = db.scalar(select(Subscription).where(Subscription.user_id == owner.id))
    owner_subscription.tier = SubscriptionTier.pro  # unlimited connected accounts
    db.commit()

    # Free tier caps at 1; this would 402 on the member's own personal
    # free subscription. Two connects, both from the member, must both
    # succeed (raise nothing) under the team's actual (pro) tier.
    first = upsert_social_account(db, member, SocialPlatform.telegram, "ca-1", access_token="t")
    second = upsert_social_account(db, member, SocialPlatform.tiktok, "ca-2", access_token="t")
    assert first.id != second.id
