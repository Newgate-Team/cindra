"""LiveAvatar key + avatars, checked against devtools/liveavatar_simulator
(which validates every request/response against LiveAvatar's own
OpenAPI spec)."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User
from app.tool_integrations import liveavatar
from devtools import liveavatar_simulator as simulator

_KEY = "la_sim_owner"


def _sim() -> TestClient:
    return TestClient(simulator.app, base_url="https://api.liveavatar.com")


@pytest.fixture(autouse=True)
def _fresh() -> None:
    simulator.state.__init__()


@pytest.fixture
def routed():
    """Route the API's outgoing LiveAvatar calls into the simulator."""
    with _sim() as sim, patch("app.tool_integrations.liveavatar.httpx.get", sim.get):
        yield sim


def _login(client: TestClient, email: str = "owner@cindra.dev", role: str = "agency") -> dict:
    client.post("/auth/register", json={"email": email, "password": "supersecret1", "role": role})
    token = client.post("/auth/login", json={"email": email, "password": "supersecret1"}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def test_client_reads_credits_and_both_avatar_lists() -> None:
    with _sim() as sim:
        assert liveavatar.get_credits(_KEY, client=sim) == 1100.0
        assert liveavatar.get_credits("la_sim_broke", client=sim) == 0.0
        with pytest.raises(liveavatar.LiveAvatarAuthError):
            liveavatar.get_credits("heygen-api-key", client=sim)
        own = liveavatar.list_avatars(_KEY, public=False, client=sim)
        assert [(a["name"], a["ready"]) for a in own["items"]] == [
            ("Мой цифровой двойник", True),
            ("Новый образ", False),  # still training: shown, not selectable
        ]
        assert own["items"][0]["default_voice_name"] == "Мой голос"
        public = liveavatar.list_avatars(_KEY, public=True, client=sim)
        assert len(public["items"]) == 3 and public["next_page"] is None
        paged = liveavatar.list_avatars(_KEY, public=True, page_size=2, client=sim)
        assert len(paged["items"]) == 2 and paged["next_page"] == 2


def test_simulator_refuses_what_liveavatar_would() -> None:
    with _sim() as sim:
        headers = {"X-API-KEY": _KEY}
        assert sim.get("/v1/avatars", params={"page_size": 500}, headers=headers).status_code == 422
        assert sim.get("/v1/avatars", params={"limit": 5}, headers=headers).status_code == 422
        typo = sim.post("/v1/sessions/token", headers=headers, json={
            "mode": "LITE", "avatar_id": simulator._PUBLIC[0][0], "max_duration": 60,
        })
        assert typo.status_code == 422


def test_connect_check_and_disconnect_through_the_api(
    client: TestClient, db: Session, routed
) -> None:
    headers = _login(client)
    wrong = client.put("/integrations/liveavatar", json={"api_key": "sk-heygen-12345"},
                       headers=headers)
    assert wrong.status_code == 400
    assert "ключ HeyGen API здесь не подойдёт" in wrong.json()["detail"]

    ok = client.put("/integrations/liveavatar", json={"api_key": f" {_KEY} "}, headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.json()["provider"] == "liveavatar"
    assert client.get("/integrations/liveavatar/account", headers=headers).json() == {
        "credits_left": 1100.0
    }
    own = client.get("/integrations/liveavatar/avatars", headers=headers).json()
    assert own["items"][0]["ready"] is True
    public = client.get("/integrations/liveavatar/avatars?ownership=public", headers=headers)
    assert public.json()["items"][0]["name"] == "Wayne"

    providers = client.get("/streams/providers", headers=headers).json()
    assert {"id": "liveavatar", "label": "LiveAvatar (HeyGen)", "connected": True} in providers

    assert client.delete("/integrations/liveavatar", headers=headers).status_code == 204
    assert client.get("/integrations/liveavatar/account", headers=headers).status_code == 404


def test_teammate_uses_but_cannot_remove_the_owners_key(
    client: TestClient, db: Session, routed
) -> None:
    owner = _login(client)
    team_id = client.post("/team", json={"name": "Команда"}, headers=owner).json()["id"]
    member = _login(client, "member@cindra.dev", role="solo")
    row = db.scalar(select(User).where(User.email == "member@cindra.dev"))
    row.team_id = team_id
    db.commit()
    client.put("/integrations/liveavatar", json={"api_key": _KEY}, headers=owner)
    assert client.get("/integrations/liveavatar/avatars", headers=member).status_code == 200
    assert client.delete("/integrations/liveavatar", headers=member).status_code == 403
