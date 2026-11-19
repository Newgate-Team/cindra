"""Cindra's HeyGen client checked against HeyGen's own OpenAPI schemas.

Our real client code talks to devtools/heygen_simulator.py, which
rejects (400) any request HeyGen's published schema wouldn't accept and
refuses to send any response the schema doesn't describe. A passing
test here means the requests we'd send to the real API are valid and
the responses we parse are shaped the way HeyGen documents them --
without a paid key.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content_pipeline import heygen_generator
from app.models import (
    GenerationJob,
    GenerationStatus,
    Post,
    SocialAccount,
    SocialPlatform,
    User,
)
from app.tool_integrations import heygen
from devtools import heygen_contract, heygen_simulator

_KEY = "sim_contract_test"


@pytest.fixture(autouse=True)
def _fresh_simulator(monkeypatch: pytest.MonkeyPatch) -> None:
    heygen_simulator.state.__init__()
    monkeypatch.setenv("HEYGEN_SIM_RENDER_SECONDS", "0")


def _sim() -> TestClient:
    # A TestClient *is* an httpx.Client, so it plugs straight into the
    # `client=` parameter every function in heygen.py takes.
    return TestClient(heygen_simulator.app, base_url="https://api.heygen.com")


def test_our_create_body_matches_heygens_schema_with_and_without_voice() -> None:
    for voice_id in ("voice_pub_ru_f", None):
        body = {
            "type": "avatar",
            "avatar_id": "lk_pub_anna",
            "script": "Привет",
            "aspect_ratio": "9:16",
            "title": None,
        }
        if voice_id:
            body["voice_id"] = voice_id
        assert heygen_contract.request_body_errors("/v3/videos", "post", body) == []


def test_schema_check_rejects_a_misspelled_field() -> None:
    body = {"type": "avatar", "avatar_id": "lk_1", "script": "x", "voice": "v"}
    assert heygen_contract.request_body_errors("/v3/videos", "post", body)


def test_key_validation_and_account() -> None:
    with _sim() as sim:
        user = heygen.get_current_user(_KEY, client=sim)
        assert heygen.account_label(user) == "simulator@heygen.local"
        assert heygen.wallet_balance(user) == (50.0, "usd")
        with pytest.raises(heygen.HeyGenAuthError):
            heygen.get_current_user("not-a-sim-key", client=sim)


def test_avatar_listing_with_our_exact_parameters() -> None:
    with _sim() as sim:
        private = heygen.list_avatar_looks(_KEY, ownership="private", client=sim)
        public = heygen.list_avatar_looks(_KEY, ownership="public", client=sim)
        everything = heygen.list_avatar_looks(_KEY, client=sim)

    statuses = {look["id"]: look["status"] for look in private["items"]}
    assert statuses == {"lk_my_twin": "completed", "lk_my_new": "processing"}
    assert all(look["status"] is None for look in public["items"])
    assert len(everything["items"]) == len(private["items"]) + len(public["items"])


def test_voice_listing_includes_own_clones_only_when_asked() -> None:
    with _sim() as sim:
        default = heygen.list_voices(_KEY, client=sim)
        own = heygen.list_voices(_KEY, voice_type="private", client=sim)
        russian = heygen.list_voices(_KEY, language="Russian", gender="female", client=sim)

    assert "voice_my_clone" not in {v["voice_id"] for v in default["items"]}
    assert [v["voice_id"] for v in own["items"]] == ["voice_my_clone"]
    assert [v["name"] for v in russian["items"]] == ["Мария"]


def test_pagination_tokens_round_trip() -> None:
    with _sim() as sim:
        seen, token = [], None
        while True:
            page = heygen.list_voices(_KEY, token=token, client=sim)
            seen += [v["voice_id"] for v in page["items"]]
            token = page["next_token"]
            if token is None:
                break
    assert len(seen) == len(set(seen)) == 4


def test_render_lifecycle_and_idempotent_replay() -> None:
    with _sim() as sim:
        first = heygen.create_avatar_video(
            _KEY,
            avatar_id="lk_my_twin",
            voice_id=None,
            script="Мой голос по умолчанию",
            aspect_ratio="9:16",
            idempotency_key="render-1",
            client=sim,
        )
        replay = heygen.create_avatar_video(
            _KEY,
            avatar_id="lk_my_twin",
            voice_id=None,
            script="Мой голос по умолчанию",
            aspect_ratio="9:16",
            idempotency_key="render-1",
            client=sim,
        )
        video = heygen.get_video(_KEY, first, client=sim)

    assert replay == first
    assert heygen_simulator.state.spent[_KEY] == 1.0  # charged once, not twice
    assert video["status"] == "completed"
    assert heygen_generator._is_allowed_result_url(video["video_url"])


def test_reusing_an_idempotency_key_for_a_different_render_is_refused() -> None:
    with _sim() as sim:
        heygen.create_avatar_video(
            _KEY, avatar_id="lk_pub_anna", voice_id=None, script="A",
            aspect_ratio="9:16", idempotency_key="k1", client=sim,
        )
        with pytest.raises(heygen.HeyGenError) as caught:
            heygen.create_avatar_video(
                _KEY, avatar_id="lk_pub_anna", voice_id=None, script="B",
                aspect_ratio="9:16", idempotency_key="k1", client=sim,
            )
    assert not isinstance(caught.value, heygen.HeyGenTransientError)


def test_untrained_avatar_and_empty_wallet_errors() -> None:
    with _sim() as sim:
        with pytest.raises(heygen.HeyGenError, match="still processing"):
            heygen.create_avatar_video(
                _KEY, avatar_id="lk_my_new", voice_id=None, script="x",
                aspect_ratio="9:16", idempotency_key="k2", client=sim,
            )
        with pytest.raises(heygen.HeyGenInsufficientCreditError):
            heygen.create_avatar_video(
                "sim_broke", avatar_id="lk_pub_anna", voice_id=None, script="x",
                aspect_ratio="9:16", idempotency_key="k3", client=sim,
            )


def _register(client: TestClient, email: str) -> dict[str, str]:
    payload = {"email": email, "password": "supersecret1", "role": "solo"}
    client.post("/auth/register", json=payload)
    token = client.post(
        "/auth/login", json={"email": email, "password": "supersecret1"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_full_flow_from_connect_to_a_scheduled_post(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The user's journey through the product, with HeyGen simulated:
    connect -> browse -> 2 variants rendered by the worker -> stored ->
    one published. Only HeyGen and the storage bucket are stand-ins."""
    settings = get_settings()
    monkeypatch.setattr(settings, "media_local_dir", str(tmp_path))
    monkeypatch.setattr(settings, "r2_public_url_base", "https://media.cindra.test")
    monkeypatch.setattr(heygen_generator, "_POLL_INTERVAL_SECONDS", 0)
    sim = _sim()
    headers = _register(client, "creator@cindra.dev")

    with (
        patch("httpx.request", sim.request),
        patch("app.content_pipeline.heygen_generator.httpx.Client", lambda: _sim()),
    ):
        connected = client.put("/integrations/heygen", json={"api_key": _KEY}, headers=headers)
        assert connected.status_code == 200, connected.text
        assert client.get("/integrations/heygen/account", headers=headers).json()[
            "remaining_balance"
        ] == 50.0

        looks = client.get(
            "/integrations/heygen/avatars?ownership=private", headers=headers
        ).json()["items"]
        usable = [look for look in looks if look["status"] in (None, "completed")]
        own_voices = client.get(
            "/integrations/heygen/voices?type=private", headers=headers
        ).json()["items"]

        created = client.post(
            "/integrations/heygen/videos",
            json={
                "script": "Привет! Это мой цифровой двойник.",
                "aspect_ratio": "9:16",
                "variants": [
                    {"avatar_id": usable[0]["id"]},
                    {"avatar_id": "lk_pub_anna", "voice_id": own_voices[0]["voice_id"]},
                ],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text

    history = client.get("/integrations/heygen/videos", headers=headers).json()["items"]
    assert [job["status"] for job in history] == ["completed", "completed"]
    assert all(job["video_url"].startswith("https://media.cindra.test/") for job in history)
    assert len(list(tmp_path.iterdir())) == 2
    assert heygen_simulator.state.spent[_KEY] == 2.0

    user = db.scalar(select(User).where(User.email == "creator@cindra.dev"))
    account = SocialAccount(
        user_id=user.id,
        platform=SocialPlatform.telegram,
        external_account_id="@channel",
        encrypted_access_token="unused",
    )
    db.add(account)
    db.commit()
    published = client.post(
        "/posts",
        json={
            "social_account_ids": [str(account.id)],
            "text": "Новый ролик",
            "video_url": history[0]["video_url"],
            "content_kind": "post",
            "generation_job_id": history[0]["id"],
            "scheduled_for": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
        headers=headers,
    )
    assert published.status_code == 201, published.text
    db.expire_all()
    post = db.scalar(select(Post))
    job = db.get(GenerationJob, post.generation_job_id)
    assert job.status == GenerationStatus.completed
    assert post.video_url == history[0]["video_url"]


def test_simulator_responses_satisfy_heygens_response_schemas() -> None:
    """The simulator asserts this on every response it sends; hitting
    each endpoint once proves none of them trips it."""
    with _sim() as sim:
        headers = {"X-Api-Key": _KEY}
        for path in (
            "/v3/users/me",
            "/v3/avatars/looks",
            "/v3/avatars/looks?ownership=private",
            "/v3/voices?type=private",
        ):
            assert sim.get(path, headers=headers).status_code == 200
        video_id = sim.post(
            "/v3/videos",
            headers=headers,
            json={"type": "avatar", "avatar_id": "lk_pub_ivan", "script": "x"},
        ).json()["data"]["video_id"]
        assert sim.get(f"/v3/videos/{video_id}", headers=headers).status_code == 200


def test_simulated_file_is_served_without_the_api_key() -> None:
    with _sim() as sim:
        video_id = heygen.create_avatar_video(
            _KEY, avatar_id="lk_pub_ivan", voice_id=None, script="x",
            aspect_ratio="16:9", idempotency_key="k9", client=sim,
        )
        url = heygen.get_video(_KEY, video_id, client=sim)["video_url"]
        response = sim.get(url)
    assert response.status_code == 200
    assert isinstance(response, httpx.Response) and response.content
