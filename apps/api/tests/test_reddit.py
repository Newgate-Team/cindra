import base64
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx
import pytest

from app.models import Post, SocialAccount, SocialPlatform
from app.social_integrations import reddit
from app.social_integrations.errors import PermanentPublishError
from app.token_crypto import encrypt_token


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _reddit_settings(**overrides) -> SimpleNamespace:
    base = {
        "reddit_client_id": "client-id",
        "reddit_client_secret": "client-secret",
        "reddit_user_agent": "cindra-test/1.0",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_exchange_code_for_token_uses_http_basic_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == reddit._TOKEN_URL
        expected = base64.b64encode(b"client-id:client-secret").decode()
        assert request.headers["authorization"] == f"Basic {expected}"
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["authorization_code"]
        assert body["code"] == ["auth-code"]
        return httpx.Response(200, json={"access_token": "access", "expires_in": 3600})

    with _client(handler) as client:
        result = reddit.exchange_code_for_token(
            "auth-code", "client-id", "client-secret", "https://example.test/callback",
            "cindra-test/1.0", client,
        )
    assert result["access_token"] == "access"


def test_get_identity_sends_bearer_and_user_agent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == reddit._ME_URL
        assert request.headers["authorization"] == "Bearer access-token"
        assert request.headers["user-agent"] == "cindra-test/1.0"
        return httpx.Response(200, json={"name": "ada_lovelace", "id": "t2_abc123"})

    with _client(handler) as client:
        identity = reddit.get_identity("access-token", "cindra-test/1.0", client)
    assert identity["name"] == "ada_lovelace"


def _account_and_post(**post_kwargs) -> tuple[SocialAccount, Post]:
    account = SocialAccount(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        platform=SocialPlatform.reddit,
        external_account_id="ada_lovelace",
        encrypted_access_token=encrypt_token("access-token"),
        encrypted_refresh_token=None,
        token_expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    defaults = {
        "id": uuid.uuid4(),
        "user_id": account.user_id,
        "social_account_id": account.id,
        "text": "Мы только что запустили новую функцию",
        "content_kind": "post",
        "scheduled_for": datetime.now(UTC),
        "platform_options": {},
    }
    defaults.update(post_kwargs)
    return account, Post(**defaults)


def test_publish_requires_text_or_media() -> None:
    account, post = _account_and_post(text="")
    with pytest.raises(PermanentPublishError, match="текста или медиа"):
        reddit.publish(account, post)


def test_publish_text_only_post_uses_self_kind() -> None:
    account, post = _account_and_post()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url}")
        assert str(request.url) == reddit._SUBMIT_URL
        assert request.headers["authorization"] == "Bearer access-token"
        assert request.headers["user-agent"] == "cindra-test/1.0"
        body = parse_qs(request.content.decode())
        assert body["kind"] == ["self"]
        assert body["sr"] == ["u_ada_lovelace"]
        assert body["title"] == ["Мы только что запустили новую функцию"]
        assert body["text"] == ["Мы только что запустили новую функцию"]
        assert "url" not in body
        return httpx.Response(
            200,
            json={"json": {"errors": [], "data": {"id": "abc123", "name": "t3_abc123"}}},
        )

    settings = _reddit_settings()
    with (
        _client(handler) as client,
        patch("app.social_integrations.reddit.get_settings", return_value=settings),
    ):
        result = reddit.publish(account, post, client)

    assert result == {"id": "t3_abc123"}
    assert calls == [f"POST {reddit._SUBMIT_URL}"]


def test_publish_with_image_uses_link_kind_without_downloading() -> None:
    account, post = _account_and_post(image_url="https://media.cindra.test/photo.jpg")

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == reddit._SUBMIT_URL
        body = parse_qs(request.content.decode())
        assert body["kind"] == ["link"]
        assert body["url"] == ["https://media.cindra.test/photo.jpg"]
        assert body["resubmit"] == ["true"]
        assert "text" not in body  # Reddit's link kind has no selftext field
        return httpx.Response(
            200,
            json={"json": {"errors": [], "data": {"id": "img1", "name": "t3_img1"}}},
        )

    settings_media = SimpleNamespace(r2_public_url_base="https://media.cindra.test")
    settings_reddit = _reddit_settings()
    with (
        _client(handler) as client,
        patch("app.social_integrations.media_validation.get_settings", return_value=settings_media),
        patch("app.social_integrations.reddit.get_settings", return_value=settings_reddit),
    ):
        result = reddit.publish(account, post, client)

    assert result == {"id": "t3_img1"}


def test_publish_rejects_media_url_outside_r2_bucket() -> None:
    account, post = _account_and_post(image_url="https://evil.example.com/image.jpg")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not submit an off-bucket URL")

    settings_media = SimpleNamespace(r2_public_url_base="https://media.cindra.test")
    with (
        _client(handler) as client,
        patch("app.social_integrations.media_validation.get_settings", return_value=settings_media),
        pytest.raises(PermanentPublishError, match="Reddit"),
    ):
        reddit.publish(account, post, client)


def test_publish_raises_on_embedded_errors_despite_200_status() -> None:
    """Reddit's own quirk: /api/submit answers HTTP 200 even when the
    post was rejected -- the real error is nested in the JSON body.
    Negative-controlled: this test (and the two publish success tests
    above, which assert errors == []) fail if the errors-array check
    in reddit.py::publish is removed."""
    account, post = _account_and_post()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "json": {
                    "errors": [["RATELIMIT", "you are doing that too much", "ratelimit"]],
                    "data": {},
                }
            },
        )

    settings = _reddit_settings()
    with (
        _client(handler) as client,
        patch("app.social_integrations.reddit.get_settings", return_value=settings),
        pytest.raises(PermanentPublishError, match="RATELIMIT"),
    ):
        reddit.publish(account, post, client)


def test_ensure_fresh_access_token_without_a_refresh_token_raises() -> None:
    account = SocialAccount(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        platform=SocialPlatform.reddit,
        external_account_id="ada_lovelace",
        encrypted_access_token=encrypt_token("stale-access"),
        encrypted_refresh_token=None,
        token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    with pytest.raises(PermanentPublishError, match="заново"):
        reddit.ensure_fresh_access_token(account)


def test_ensure_fresh_access_token_refreshes_when_a_refresh_token_exists() -> None:
    account = SocialAccount(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        platform=SocialPlatform.reddit,
        external_account_id="ada_lovelace",
        encrypted_access_token=encrypt_token("stale-access"),
        encrypted_refresh_token=encrypt_token("refresh-token"),
        token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        expected = base64.b64encode(b"client-id:client-secret").decode()
        assert request.headers["authorization"] == f"Basic {expected}"
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["refresh_token"]
        assert body["refresh_token"] == ["refresh-token"]
        return httpx.Response(200, json={"access_token": "fresh-access", "expires_in": 3600})

    settings = _reddit_settings()
    with (
        _client(handler) as client,
        patch("app.social_integrations.reddit.get_settings", return_value=settings),
    ):
        token = reddit.ensure_fresh_access_token(account, client)
    assert token == "fresh-access"
