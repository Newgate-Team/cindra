import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from app.models import Post, SocialAccount, SocialPlatform
from app.social_integrations import twitter
from app.social_integrations.errors import PermanentPublishError
from app.token_crypto import encrypt_token


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _twitter_settings(**overrides) -> SimpleNamespace:
    base = {"twitter_client_id": "client-id", "twitter_client_secret": "client-secret"}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_exchange_code_for_token_sends_pkce_verifier_and_basic_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == twitter._TOKEN_URL
        expected = base64.b64encode(b"client-id:client-secret").decode()
        assert request.headers["authorization"] == f"Basic {expected}"
        body = request.read().decode()
        assert "code_verifier=verifier-123" in body
        assert "grant_type=authorization_code" in body
        return httpx.Response(
            200, json={"access_token": "access", "refresh_token": "refresh", "expires_in": 7200}
        )

    with _client(handler) as client:
        result = twitter.exchange_code_for_token(
            "auth-code", "verifier-123", "client-id", "client-secret",
            "https://example.test/callback", client,
        )
    assert result["access_token"] == "access"


def test_get_me_returns_data_object() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == twitter._ME_URL
        assert request.headers["authorization"] == "Bearer access-token"
        return httpx.Response(200, json={"data": {"id": "12345", "username": "ada"}})

    with _client(handler) as client:
        me = twitter.get_me("access-token", client)
    assert me["id"] == "12345"


def test_get_me_raises_when_data_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    with (
        _client(handler) as client,
        pytest.raises(PermanentPublishError, match="данные пользователя"),
    ):
        twitter.get_me("access-token", client)


def _account_and_post(**post_kwargs) -> tuple[SocialAccount, Post]:
    account = SocialAccount(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        platform=SocialPlatform.twitter,
        external_account_id="12345",
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


def test_publish_rejects_image() -> None:
    account, post = _account_and_post(image_url="https://media.cindra.test/photo.jpg")
    with pytest.raises(PermanentPublishError, match="изображений и видео"):
        twitter.publish(account, post)


def test_publish_rejects_video() -> None:
    account, post = _account_and_post(video_url="https://media.cindra.test/video.mp4")
    with pytest.raises(PermanentPublishError, match="изображений и видео"):
        twitter.publish(account, post)


def test_publish_requires_text() -> None:
    account, post = _account_and_post(text="")
    with pytest.raises(PermanentPublishError, match="текста"):
        twitter.publish(account, post)


def test_publish_posts_tweet_truncated_to_280_chars() -> None:
    account, post = _account_and_post(text="ф" * 400)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url}")
        assert str(request.url) == twitter._TWEETS_URL
        assert request.headers["authorization"] == "Bearer access-token"
        body = json.loads(request.content)
        assert len(body["text"]) == 280
        return httpx.Response(201, json={"data": {"id": "999", "text": body["text"]}})

    with _client(handler) as client:
        result = twitter.publish(account, post, client)

    assert result == {"id": "999"}
    assert calls == [f"POST {twitter._TWEETS_URL}"]


def test_publish_raises_on_4xx_status() -> None:
    account, post = _account_and_post()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"title": "Forbidden", "detail": "not enough access"})

    with (
        _client(handler) as client,
        pytest.raises(PermanentPublishError, match="not enough access"),
    ):
        twitter.publish(account, post, client)


def test_ensure_fresh_access_token_without_a_refresh_token_raises() -> None:
    account = SocialAccount(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        platform=SocialPlatform.twitter,
        external_account_id="12345",
        encrypted_access_token=encrypt_token("stale-access"),
        encrypted_refresh_token=None,
        token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    with pytest.raises(PermanentPublishError, match="заново"):
        twitter.ensure_fresh_access_token(account)


def test_ensure_fresh_access_token_refreshes_when_a_refresh_token_exists() -> None:
    account = SocialAccount(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        platform=SocialPlatform.twitter,
        external_account_id="12345",
        encrypted_access_token=encrypt_token("stale-access"),
        encrypted_refresh_token=encrypt_token("refresh-token"),
        token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        assert "grant_type=refresh_token" in body
        assert "refresh_token=refresh-token" in body
        return httpx.Response(
            200, json={"access_token": "fresh-access", "refresh_token": "new-refresh", "expires_in": 7200}
        )

    settings = _twitter_settings()
    with (
        _client(handler) as client,
        patch("app.social_integrations.twitter.get_settings", return_value=settings),
    ):
        token = twitter.ensure_fresh_access_token(account, client)
    assert token == "fresh-access"
