import json
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.integrations import get_secret
from app.models import (
    GenerationContentType,
    GenerationJob,
    GenerationStatus,
    Integration,
    IntegrationProvider,
    UsageEvent,
    UsageEventType,
    User,
)
from app.tool_integrations import heygen

_HEYGEN_USER = {
    "username": "jane",
    "email": "jane@example.com",
    "billing_type": "wallet",
    "wallet": {"currency": "usd", "remaining_balance": 12.5},
}


def _register_and_login(client: TestClient, email: str, role: str = "agency") -> dict[str, str]:
    payload = {"email": email, "password": "supersecret1", "role": role}
    client.post("/auth/register", json=payload)
    token = client.post(
        "/auth/login", json={"email": email, "password": "supersecret1"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _connect(client: TestClient, headers: dict[str, str], api_key: str = "hg-secret-key"):
    with patch(
        "app.routers.integrations.heygen.get_current_user", return_value=_HEYGEN_USER
    ) as validate:
        response = client.put("/integrations/heygen", json={"api_key": api_key}, headers=headers)
    return response, validate


def _rows(db: Session) -> list[Integration]:
    db.expire_all()
    return list(db.scalars(select(Integration)))


def test_connect_validates_then_stores_key_encrypted(client: TestClient, db: Session) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")

    response, validate = _connect(client, headers, api_key="  hg-secret-key\n")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "heygen"
    assert body["account_label"] == "jane@example.com"
    assert body["is_own"] is True
    assert "hg-secret-key" not in response.text
    # Stripped before validation, so a pasted trailing newline isn't
    # reported as an invalid key.
    validate.assert_called_once_with("hg-secret-key")

    (row,) = _rows(db)
    assert row.encrypted_secret != "hg-secret-key"
    assert get_secret(row) == "hg-secret-key"


def test_connect_with_rejected_key_stores_nothing(client: TestClient, db: Session) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    with patch(
        "app.routers.integrations.heygen.get_current_user",
        side_effect=heygen.HeyGenAuthError("Invalid or expired API key"),
    ):
        response = client.put(
            "/integrations/heygen", json={"api_key": "wrong-key-123"}, headers=headers
        )

    assert response.status_code == 400
    assert "не принял этот ключ" in response.json()["detail"]
    assert _rows(db) == []


def test_connect_when_heygen_is_down_returns_503_and_stores_nothing(
    client: TestClient, db: Session
) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    with patch(
        "app.routers.integrations.heygen.get_current_user",
        side_effect=heygen.HeyGenTransientError("HeyGen 503"),
    ):
        response = client.put(
            "/integrations/heygen", json={"api_key": "hg-secret-key"}, headers=headers
        )

    assert response.status_code == 503
    assert _rows(db) == []


def test_reconnecting_replaces_the_key_in_the_same_row(client: TestClient, db: Session) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    _connect(client, headers, api_key="first-key-123")
    _connect(client, headers, api_key="second-key-456")

    (row,) = _rows(db)
    assert get_secret(row) == "second-key-456"


def test_list_and_disconnect(client: TestClient, db: Session) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    assert client.get("/integrations", headers=headers).json() == []

    _connect(client, headers)
    listed = client.get("/integrations", headers=headers).json()
    assert [item["provider"] for item in listed] == ["heygen"]
    assert "encrypted_secret" not in listed[0]

    assert client.delete("/integrations/heygen", headers=headers).status_code == 204
    assert client.get("/integrations", headers=headers).json() == []
    assert client.delete("/integrations/heygen", headers=headers).status_code == 404


def test_avatars_and_voices_require_a_connection(client: TestClient) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    assert client.get("/integrations/heygen/avatars", headers=headers).status_code == 404
    assert client.get("/integrations/heygen/voices", headers=headers).status_code == 404
    assert client.get("/integrations/heygen/account", headers=headers).status_code == 404


def test_avatars_are_fetched_with_the_stored_key(client: TestClient) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    _connect(client, headers, api_key="hg-secret-key")
    page = {
        "items": [
            {
                "id": "lk_1",
                "name": "Look",
                "avatar_type": "digital_twin",
                "gender": None,
                "preview_image_url": "https://files.heygen.ai/look/1.jpg",
                "preview_video_url": None,
                "default_voice_id": "voice-1",
            }
        ],
        "next_token": "next",
    }
    with patch(
        "app.routers.integrations.heygen.list_avatar_looks", return_value=page
    ) as list_looks:
        response = client.get(
            "/integrations/heygen/avatars?ownership=private&token=abc", headers=headers
        )

    assert response.status_code == 200
    assert response.json() == page
    list_looks.assert_called_once_with("hg-secret-key", token="abc", ownership="private")


def test_voices_are_fetched_with_filters(client: TestClient) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    _connect(client, headers, api_key="hg-secret-key")
    with patch(
        "app.routers.integrations.heygen.list_voices",
        return_value={"items": [], "next_token": None},
    ) as list_voices:
        response = client.get(
            "/integrations/heygen/voices?language=Russian&gender=female", headers=headers
        )

    assert response.status_code == 200
    list_voices.assert_called_once_with(
        "hg-secret-key", token=None, language="Russian", gender="female"
    )


def test_account_reports_wallet_balance(client: TestClient) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    _connect(client, headers)
    with patch("app.routers.integrations.heygen.get_current_user", return_value=_HEYGEN_USER):
        response = client.get("/integrations/heygen/account", headers=headers)

    assert response.json() == {
        "account_label": "jane@example.com",
        "billing_type": "wallet",
        "remaining_balance": 12.5,
        "currency": "usd",
    }


def test_key_revoked_after_connecting_tells_the_user_to_reconnect(client: TestClient) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    _connect(client, headers)
    with patch(
        "app.routers.integrations.heygen.list_avatar_looks",
        side_effect=heygen.HeyGenAuthError("Invalid or expired API key"),
    ):
        response = client.get("/integrations/heygen/avatars", headers=headers)

    assert response.status_code == 400
    assert "подключите HeyGen заново" in response.json()["detail"]


def test_endpoints_require_auth(client: TestClient) -> None:
    assert client.get("/integrations").status_code == 401
    assert client.put("/integrations/heygen", json={"api_key": "hg-secret-key"}).status_code == 401
    assert client.delete("/integrations/heygen").status_code == 401
    assert client.get("/integrations/heygen/avatars").status_code == 401


def _make_team(client: TestClient, db: Session) -> tuple[dict[str, str], dict[str, str]]:
    owner_headers = _register_and_login(client, "owner@cindra.dev")
    team_id = client.post("/team", json={"name": "Команда"}, headers=owner_headers).json()["id"]
    member_headers = _register_and_login(client, "member@cindra.dev", role="solo")
    member = db.scalar(select(User).where(User.email == "member@cindra.dev"))
    member.team_id = team_id
    db.commit()
    return owner_headers, member_headers


def test_teammate_generates_with_the_owners_connection(client: TestClient, db: Session) -> None:
    owner_headers, member_headers = _make_team(client, db)
    _connect(client, owner_headers, api_key="owners-key-123")

    listed = client.get("/integrations", headers=member_headers).json()
    assert len(listed) == 1
    assert listed[0]["is_own"] is False

    with patch(
        "app.routers.integrations.heygen.list_voices",
        return_value={"items": [], "next_token": None},
    ) as list_voices:
        client.get("/integrations/heygen/voices", headers=member_headers)
    assert list_voices.call_args.args[0] == "owners-key-123"


def test_members_own_key_takes_precedence_over_the_owners(
    client: TestClient, db: Session
) -> None:
    owner_headers, member_headers = _make_team(client, db)
    _connect(client, owner_headers, api_key="owners-key-123")
    _connect(client, member_headers, api_key="members-key-456")

    assert len(_rows(db)) == 2
    with patch(
        "app.routers.integrations.heygen.list_voices",
        return_value={"items": [], "next_token": None},
    ) as list_voices:
        client.get("/integrations/heygen/voices", headers=member_headers)
    assert list_voices.call_args.args[0] == "members-key-456"


def test_outsider_cannot_see_or_use_another_teams_connection(
    client: TestClient, db: Session
) -> None:
    owner_headers, _member_headers = _make_team(client, db)
    _connect(client, owner_headers)
    outsider_headers = _register_and_login(client, "outsider@cindra.dev")

    assert client.get("/integrations", headers=outsider_headers).json() == []
    assert client.get("/integrations/heygen/voices", headers=outsider_headers).status_code == 404
    assert client.delete("/integrations/heygen", headers=outsider_headers).status_code == 404
    assert len(_rows(db)) == 1
    assert _rows(db)[0].provider == IntegrationProvider.heygen


_VARIANTS = [
    {"avatar_id": "lk_1", "voice_id": "voice-1"},
    {"avatar_id": "lk_2", "voice_id": "voice-2"},
]


def _create_videos(client: TestClient, headers: dict[str, str], **overrides):
    body = {"script": "Привет! Это тестовый ролик.", "aspect_ratio": "9:16", "variants": _VARIANTS}
    body.update(overrides)
    with patch("app.routers.integrations.run_generation_job.delay") as delay:
        response = client.post("/integrations/heygen/videos", json=body, headers=headers)
    return response, delay


def _jobs(db: Session) -> list[GenerationJob]:
    db.expire_all()
    return list(db.scalars(select(GenerationJob)))


def _usage(db: Session) -> int:
    db.expire_all()
    return db.scalar(
        select(func.count())
        .select_from(UsageEvent)
        .where(UsageEvent.event_type == UsageEventType.byo_avatar_video)
    )


def test_create_videos_requires_a_connection(client: TestClient, db: Session) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    response, delay = _create_videos(client, headers)
    assert response.status_code == 404
    delay.assert_not_called()
    assert _jobs(db) == []


def test_create_videos_makes_one_job_per_variant(client: TestClient, db: Session) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    _connect(client, headers, api_key="hg-secret-key")

    response, delay = _create_videos(client, headers)

    assert response.status_code == 201
    body = response.json()
    assert [item["avatar_id"] for item in body] == ["lk_1", "lk_2"]
    assert len({item["batch_id"] for item in body}) == 1
    assert all(item["status"] == "queued" for item in body)
    assert delay.call_count == 2
    assert _usage(db) == 2

    jobs = _jobs(db)
    assert {job.input_payload["provider"] for job in jobs} == {"heygen"}
    # One Idempotency-Key per render, never shared between variants.
    assert len({job.input_payload["heygen_request_id"] for job in jobs}) == 2
    # The key itself is looked up by the worker, never copied into jobs.
    assert all("hg-secret-key" not in json.dumps(job.input_payload) for job in jobs)


def test_flagged_script_is_rejected_before_anything_is_spent(
    client: TestClient, db: Session
) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    _connect(client, headers)

    response, delay = _create_videos(client, headers, script="ну ты и сука")

    assert response.status_code == 400
    delay.assert_not_called()
    assert _jobs(db) == []
    assert _usage(db) == 0


def test_more_than_four_variants_is_rejected(client: TestClient, db: Session) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    _connect(client, headers)
    response, _delay = _create_videos(
        client, headers, variants=[{"avatar_id": f"lk_{i}", "voice_id": "v"} for i in range(5)]
    )
    assert response.status_code == 422
    assert _jobs(db) == []


def test_monthly_quota_is_checked_for_the_whole_batch(client: TestClient, db: Session) -> None:
    headers = _register_and_login(client, "owner@cindra.dev")
    _connect(client, headers)
    four = [{"avatar_id": f"lk_{i}", "voice_id": "v"} for i in range(4)]

    assert _create_videos(client, headers, variants=four)[0].status_code == 201
    # Free tier allows 5: a 2-variant batch must be refused whole, not
    # half-rendered.
    response, delay = _create_videos(client, headers)

    assert response.status_code == 402
    assert "аватар-видео HeyGen" in response.json()["detail"]
    delay.assert_not_called()
    assert len(_jobs(db)) == 4
    assert _usage(db) == 4


def test_list_videos_shows_only_heygen_jobs_visible_to_the_user(
    client: TestClient, db: Session
) -> None:
    owner_headers, member_headers = _make_team(client, db)
    _connect(client, owner_headers)
    _create_videos(client, owner_headers)
    owner = db.scalar(select(User).where(User.email == "owner@cindra.dev"))
    db.add(
        GenerationJob(
            user_id=owner.id,
            content_type=GenerationContentType.video,
            input_payload={"topic": "обычное видео"},
        )
    )
    db.commit()

    page = client.get("/integrations/heygen/videos", headers=member_headers).json()
    assert page["total"] == 2
    assert {item["avatar_id"] for item in page["items"]} == {"lk_1", "lk_2"}

    outsider = _register_and_login(client, "outsider@cindra.dev")
    assert client.get("/integrations/heygen/videos", headers=outsider).json()["total"] == 0


def test_content_generate_cannot_route_a_job_to_someones_heygen_key(
    client: TestClient, db: Session
) -> None:
    """The worker trusts input_payload["heygen_integration_id"] to pick
    whose wallet pays -- only /integrations/heygen/videos may set it.
    /content/generate's request model must keep dropping unknown keys."""
    headers = _register_and_login(client, "owner@cindra.dev")
    _connect(client, headers)
    integration_id = str(_rows(db)[0].id)

    with patch("app.routers.content.run_generation_job.delay"):
        response = client.post(
            "/content/generate",
            json={
                "topic": "тема",
                "content_type": "text",
                "provider": "heygen",
                "heygen_integration_id": integration_id,
            },
            headers=headers,
        )

    assert response.status_code in (200, 201, 202)
    (job,) = _jobs(db)
    assert "provider" not in job.input_payload
    assert "heygen_integration_id" not in job.input_payload


def test_avatar_videos_never_appear_in_the_shared_feed(client: TestClient, db: Session) -> None:
    viewer = _register_and_login(client, "viewer@cindra.dev")
    _register_and_login(client, "solo@cindra.dev", role="solo")
    solo = db.scalar(select(User).where(User.email == "solo@cindra.dev"))
    assert solo.share_generations_to_feed is True
    for payload in (
        {"topic": "обычное видео"},
        {"provider": "heygen", "script": "моё лицо"},
    ):
        db.add(
            GenerationJob(
                user_id=solo.id,
                content_type=GenerationContentType.video,
                status=GenerationStatus.completed,
                input_payload=payload,
                output_payload={"video_url": "https://media.cindra.test/v.mp4"},
            )
        )
    db.commit()

    items = client.get("/feed", headers=viewer).json()["items"]
    assert [item["caption"] for item in items] == ["обычное видео"]
