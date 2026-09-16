import io
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx
import pytest

from app.models import Post, SocialAccount, SocialPlatform
from app.social_integrations import linkedin
from app.social_integrations.errors import PermanentPublishError
from app.token_crypto import encrypt_token


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _linkedin_settings(**overrides) -> SimpleNamespace:
    base = {"linkedin_api_version": "202501"}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_exchange_code_for_token_uses_form_encoded_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == linkedin._TOKEN_URL
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["authorization_code"]
        assert body["code"] == ["auth-code"]
        assert body["client_id"] == ["client-id"]
        return httpx.Response(
            200, json={"access_token": "access", "expires_in": 5184000}
        )

    with _client(handler) as client:
        result = linkedin.exchange_code_for_token(
            "auth-code", "client-id", "client-secret", "https://example.test/callback", client
        )
    assert result["access_token"] == "access"


def test_get_member_info_returns_userinfo_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == linkedin._USERINFO_URL
        assert request.headers["authorization"] == "Bearer access-token"
        return httpx.Response(200, json={"sub": "abc123", "name": "Ada Lovelace"})

    with _client(handler) as client:
        member = linkedin.get_member_info("access-token", client)
    assert member["sub"] == "abc123"


def test_download_image_rejects_url_outside_r2_bucket() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not fetch an off-bucket URL")

    settings = SimpleNamespace(r2_public_url_base="https://media.cindra.test")
    with (
        _client(handler) as client,
        patch("app.social_integrations.media_validation.get_settings", return_value=settings),
        pytest.raises(PermanentPublishError, match="LinkedIn"),
    ):
        linkedin._download_image("https://evil.example.com/image.jpg", io.BytesIO(), client)


def test_download_image_accepts_r2_bucket_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://media.cindra.test/image.jpg"
        return httpx.Response(200, content=b"image-bytes", headers={"content-type": "image/jpeg"})

    settings = SimpleNamespace(r2_public_url_base="https://media.cindra.test")
    with (
        _client(handler) as client,
        patch("app.social_integrations.media_validation.get_settings", return_value=settings),
    ):
        size, content_type = linkedin._download_image(
            "https://media.cindra.test/image.jpg", io.BytesIO(), client
        )
    assert size == len(b"image-bytes")
    assert content_type == "image/jpeg"


def _account_and_post(**post_kwargs) -> tuple[SocialAccount, Post]:
    account = SocialAccount(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        platform=SocialPlatform.linkedin,
        external_account_id="abc123",
        encrypted_access_token=encrypt_token("access-token"),
        encrypted_refresh_token=None,
        token_expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    defaults = {
        "id": uuid.uuid4(),
        "user_id": account.user_id,
        "social_account_id": account.id,
        "text": "Рады объявить о запуске новой функции",
        "content_kind": "post",
        "scheduled_for": datetime.now(UTC),
        "platform_options": {},
    }
    defaults.update(post_kwargs)
    return account, Post(**defaults)


def test_publish_rejects_video() -> None:
    account, post = _account_and_post(video_url="https://media.cindra.test/video.mp4")
    with pytest.raises(PermanentPublishError, match="видео"):
        linkedin.publish(account, post)


def test_publish_requires_text() -> None:
    account, post = _account_and_post(text="")
    with pytest.raises(PermanentPublishError, match="текста"):
        linkedin.publish(account, post)


def test_publish_text_only_post() -> None:
    account, post = _account_and_post()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url}")
        assert str(request.url) == linkedin._POSTS_URL
        assert request.headers["linkedin-version"] == "202501"
        assert request.headers["x-restli-protocol-version"] == "2.0.0"
        body = request.read().decode()
        assert '"author":"urn:li:person:abc123"' in body
        assert '"commentary":"Рады объявить о запуске новой функции"' in body
        assert "content" not in body  # no image -- no media block at all
        return httpx.Response(201, headers={"x-restli-id": "urn:li:share:999"}, content=b"")

    settings = _linkedin_settings()
    with (
        _client(handler) as client,
        patch("app.social_integrations.linkedin.get_settings", return_value=settings),
    ):
        result = linkedin.publish(account, post, client)

    assert result == {"id": "urn:li:share:999"}
    assert calls == [f"POST {linkedin._POSTS_URL}"]


def test_publish_with_image_uploads_then_posts() -> None:
    account, post = _account_and_post(image_url="https://media.cindra.test/photo.jpg")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url}")
        if str(request.url) == post.image_url:
            return httpx.Response(200, content=b"img-bytes", headers={"content-type": "image/png"})
        if str(request.url) == f"{linkedin._IMAGES_URL}?action=initializeUpload":
            body = request.read().decode()
            assert '"owner":"urn:li:person:abc123"' in body
            return httpx.Response(
                200,
                json={
                    "value": {
                        "uploadUrl": "https://upload.linkedin.test/image-1",
                        "image": "urn:li:image:img-1",
                    }
                },
            )
        if str(request.url) == "https://upload.linkedin.test/image-1":
            assert request.read() == b"img-bytes"
            return httpx.Response(201)
        if str(request.url) == linkedin._POSTS_URL:
            body = request.read().decode()
            assert '"content":{"media":{"id":"urn:li:image:img-1"}}' in body
            return httpx.Response(201, headers={"x-restli-id": "urn:li:share:1000"}, content=b"")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    settings_media = SimpleNamespace(r2_public_url_base="https://media.cindra.test")
    settings_linkedin = _linkedin_settings()
    with (
        _client(handler) as client,
        patch("app.social_integrations.media_validation.get_settings", return_value=settings_media),
        patch("app.social_integrations.linkedin.get_settings", return_value=settings_linkedin),
    ):
        result = linkedin.publish(account, post, client)

    assert result == {"id": "urn:li:share:1000"}
    assert calls[-1] == f"POST {linkedin._POSTS_URL}"


def test_init_image_upload_requires_upload_url_and_image_urn() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": {}})

    settings = _linkedin_settings()
    with (
        _client(handler) as client,
        patch("app.social_integrations.linkedin.get_settings", return_value=settings),
        pytest.raises(PermanentPublishError, match="адрес или id"),
    ):
        linkedin._init_image_upload("access-token", "urn:li:person:abc123", client)


def test_ensure_fresh_access_token_without_a_refresh_token_raises() -> None:
    account = SocialAccount(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        platform=SocialPlatform.linkedin,
        external_account_id="abc123",
        encrypted_access_token=encrypt_token("stale-access"),
        encrypted_refresh_token=None,
        token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    with pytest.raises(PermanentPublishError, match="заново"):
        linkedin.ensure_fresh_access_token(account)


def test_ensure_fresh_access_token_refreshes_when_a_refresh_token_exists() -> None:
    account = SocialAccount(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        platform=SocialPlatform.linkedin,
        external_account_id="abc123",
        encrypted_access_token=encrypt_token("stale-access"),
        encrypted_refresh_token=encrypt_token("refresh-token"),
        token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = parse_qs(request.content.decode())
        assert body["refresh_token"] == ["refresh-token"]
        return httpx.Response(200, json={"access_token": "fresh-access", "expires_in": 5184000})

    settings = SimpleNamespace(linkedin_client_id="client-id", linkedin_client_secret="secret")
    with (
        _client(handler) as client,
        patch("app.social_integrations.linkedin.get_settings", return_value=settings),
    ):
        token = linkedin.ensure_fresh_access_token(account, client)
    assert token == "fresh-access"
