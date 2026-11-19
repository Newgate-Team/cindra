import json

import httpx
import pytest

from app.tool_integrations import heygen


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_get_current_user_sends_key_in_header_not_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.heygen.com/v3/users/me"
        assert request.headers["x-api-key"] == "hg-key"
        assert "hg-key" not in str(request.url)
        return httpx.Response(
            200,
            json={
                "data": {
                    "username": "jane",
                    "email": "jane@example.com",
                    "billing_type": "wallet",
                    "wallet": {"currency": "usd", "remaining_balance": 42.5},
                }
            },
        )

    with _client(handler) as client:
        user = heygen.get_current_user("hg-key", client)

    assert heygen.account_label(user) == "jane@example.com"
    assert heygen.wallet_balance(user) == (42.5, "usd")


def test_account_label_falls_back_to_username() -> None:
    assert heygen.account_label({"username": "jane"}) == "jane"


def test_wallet_balance_absent_for_non_wallet_billing() -> None:
    assert heygen.wallet_balance({"billing_type": "subscription", "wallet": None}) == (
        None,
        None,
    )


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_key_raises_auth_error(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, json={"error": {"code": "unauthorized", "message": "Invalid or expired API key"}}
        )

    with _client(handler) as client, pytest.raises(heygen.HeyGenAuthError):
        heygen.get_current_user("bad-key", client)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_rate_limit_and_5xx_are_transient(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "slow down"}})

    with _client(handler) as client, pytest.raises(heygen.HeyGenTransientError):
        heygen.get_current_user("hg-key", client)


def test_network_failure_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with _client(handler) as client, pytest.raises(heygen.HeyGenTransientError):
        heygen.get_current_user("hg-key", client)


def test_other_4xx_is_permanent_with_heygen_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"code": "invalid_parameter", "message": "limit must be <= 50"}},
        )

    with _client(handler) as client, pytest.raises(heygen.HeyGenError, match="limit must be"):
        heygen.get_current_user("hg-key", client)


def test_list_avatar_looks_normalizes_and_paginates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/avatars/looks"
        assert request.url.params["token"] == "page-2"
        assert request.url.params["ownership"] == "private"
        assert request.url.params["limit"] == "50"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "lk_1",
                        "name": "Business Suit",
                        "avatar_type": "digital_twin",
                        "gender": "female",
                        "preview_image_url": "https://files.heygen.ai/look/1.jpg",
                        "preview_video_url": "https://files.heygen.ai/look/1.mp4",
                        "default_voice_id": "voice-1",
                        "tags": ["ignored"],
                        "status": "processing",
                    },
                    {"name": "no id -- skipped"},
                ],
                "has_more": True,
                "next_token": "page-3",
            },
        )

    with _client(handler) as client:
        page = heygen.list_avatar_looks("hg-key", token="page-2", ownership="private", client=client)

    assert page["next_token"] == "page-3"
    assert page["items"] == [
        {
            "id": "lk_1",
            "name": "Business Suit",
            "avatar_type": "digital_twin",
            "gender": "female",
            "preview_image_url": "https://files.heygen.ai/look/1.jpg",
            "preview_video_url": "https://files.heygen.ai/look/1.mp4",
            "default_voice_id": "voice-1",
            "status": "processing",
        }
    ]


def test_list_avatar_looks_omits_unset_params_and_last_page_has_no_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "token" not in request.url.params
        assert "ownership" not in request.url.params
        return httpx.Response(200, json={"data": [], "has_more": False, "next_token": "stale"})

    with _client(handler) as client:
        page = heygen.list_avatar_looks("hg-key", client=client)

    assert page == {"items": [], "next_token": None}


def test_list_voices_normalizes_and_filters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/voices"
        assert request.url.params["language"] == "Russian"
        assert request.url.params["gender"] == "male"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "voice_id": "v-1",
                        "name": "Ivan",
                        "language": "Russian",
                        "gender": "male",
                        "preview_audio_url": "https://files.heygen.ai/voice/1.mp3",
                        "available_engines": ["starfish"],
                    }
                ],
                "has_more": False,
            },
        )

    with _client(handler) as client:
        page = heygen.list_voices("hg-key", language="Russian", gender="male", client=client)

    assert page["items"] == [
        {
            "voice_id": "v-1",
            "name": "Ivan",
            "language": "Russian",
            "gender": "male",
            "preview_audio_url": "https://files.heygen.ai/voice/1.mp3",
        }
    ]
    assert page["next_token"] is None


def test_create_avatar_video_sends_idempotency_key_and_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v3/videos"
        assert request.headers["idempotency-key"] == "req-1"
        assert request.headers["x-api-key"] == "hg-key"
        assert json.loads(request.content) == {
            "type": "avatar",
            "avatar_id": "lk_1",
            "voice_id": "voice-1",
            "script": "Привет!",
            "aspect_ratio": "9:16",
            "title": "Ролик",
        }
        return httpx.Response(200, json={"data": {"video_id": "v_1", "status": "waiting"}})

    with _client(handler) as client:
        video_id = heygen.create_avatar_video(
            "hg-key",
            avatar_id="lk_1",
            voice_id="voice-1",
            script="Привет!",
            aspect_ratio="9:16",
            idempotency_key="req-1",
            title="Ролик",
            client=client,
        )
    assert video_id == "v_1"


def test_create_avatar_video_without_id_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {}})

    with _client(handler) as client, pytest.raises(heygen.HeyGenError, match="video_id"):
        heygen.create_avatar_video(
            "hg-key",
            avatar_id="lk_1",
            voice_id="v",
            script="s",
            aspect_ratio="9:16",
            idempotency_key="k",
            client=client,
        )


def test_insufficient_credit_has_its_own_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402, json={"error": {"code": "insufficient_credit", "message": "not enough credits"}}
        )

    with _client(handler) as client, pytest.raises(heygen.HeyGenInsufficientCreditError):
        heygen.get_current_user("hg-key", client)


def test_idempotent_request_still_in_progress_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409, json={"error": {"code": "request_in_progress", "message": "still running"}}
        )

    with _client(handler) as client, pytest.raises(heygen.HeyGenTransientError):
        heygen.get_current_user("hg-key", client)


def test_other_409_is_permanent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409, json={"error": {"code": "resource_not_ready", "message": "avatar processing"}}
        )

    with _client(handler) as client, pytest.raises(heygen.HeyGenError) as caught:
        heygen.get_current_user("hg-key", client)
    assert not isinstance(caught.value, heygen.HeyGenTransientError)


def test_get_video_escapes_the_id_in_the_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path == b"/v3/videos/..%2Fusers%2Fme"
        return httpx.Response(200, json={"data": {"status": "processing"}})

    with _client(handler) as client:
        assert heygen.get_video("hg-key", "../users/me", client)["status"] == "processing"
