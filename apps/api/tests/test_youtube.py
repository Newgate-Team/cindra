import io
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx
import pytest

from app.models import Post, SocialAccount, SocialPlatform
from app.social_integrations import youtube
from app.social_integrations.errors import PermanentPublishError
from app.token_crypto import encrypt_token


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_exchange_code_for_token_uses_form_encoded_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == youtube._TOKEN_URL
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["authorization_code"]
        assert body["code"] == ["auth-code"]
        assert body["client_id"] == ["client-id"]
        return httpx.Response(
            200,
            json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600},
        )

    with _client(handler) as client:
        result = youtube.exchange_code_for_token(
            "auth-code", "client-id", "client-secret", "https://example.test/callback", client
        )
    assert result["access_token"] == "access"


def test_refresh_access_token_uses_refresh_grant() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["refresh_token"]
        assert body["refresh_token"] == ["old-refresh"]
        return httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600})

    with _client(handler) as client:
        result = youtube.refresh_access_token("old-refresh", "client-id", "client-secret", client)
    assert result["access_token"] == "new-access"


def test_get_channel_info_returns_first_channel() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/youtube/v3/channels"
        assert request.url.params["mine"] == "true"
        return httpx.Response(
            200,
            json={"items": [{"id": "UC123", "snippet": {"title": "Cindra Demo"}}]},
        )

    with _client(handler) as client:
        channel = youtube.get_channel_info("access-token", client)
    assert channel["id"] == "UC123"


def test_get_channel_info_raises_when_no_channels() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    with _client(handler) as client, pytest.raises(PermanentPublishError, match="канал"):
        youtube.get_channel_info("access-token", client)


def test_download_video_rejects_url_outside_r2_bucket() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not fetch an off-bucket URL")

    settings = SimpleNamespace(r2_public_url_base="https://media.cindra.test")
    with (
        _client(handler) as client,
        patch("app.social_integrations.media_validation.get_settings", return_value=settings),
        pytest.raises(PermanentPublishError, match="YouTube"),
    ):
        youtube._download_video("https://evil.example.com/video.mp4", io.BytesIO(), client)


def test_download_video_accepts_r2_bucket_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://media.cindra.test/video.mp4"
        return httpx.Response(200, content=b"video-bytes", headers={"content-type": "video/mp4"})

    settings = SimpleNamespace(r2_public_url_base="https://media.cindra.test")
    with (
        _client(handler) as client,
        patch("app.social_integrations.media_validation.get_settings", return_value=settings),
    ):
        size, content_type = youtube._download_video(
            "https://media.cindra.test/video.mp4", io.BytesIO(), client
        )
    assert size == len(b"video-bytes")
    assert content_type == "video/mp4"


def _account_and_post(platform_options: dict) -> tuple[SocialAccount, Post]:
    account = SocialAccount(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        platform=SocialPlatform.youtube,
        external_account_id="UC123",
        encrypted_access_token=encrypt_token("access-token"),
        encrypted_refresh_token=encrypt_token("refresh-token"),
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    post = Post(
        id=uuid.uuid4(),
        user_id=account.user_id,
        social_account_id=account.id,
        text="Осенняя коллекция уже здесь",
        video_url="https://media.cindra.test/video.mp4",
        content_kind="post",
        platform_options=platform_options,
        scheduled_for=datetime.now(UTC),
    )
    return account, post


def test_publish_requires_video_url() -> None:
    account, post = _account_and_post({})
    post.video_url = None
    with pytest.raises(PermanentPublishError, match="video_url"):
        youtube.publish(account, post)


def test_publish_uploads_video_via_resumable_session() -> None:
    account, post = _account_and_post(
        {
            "youtube": {
                "accounts": {
                    # keyed by account id, filled in below
                }
            }
        }
    )
    post.platform_options["youtube"]["accounts"][str(account.id)] = {
        "title": "Осенняя коллекция",
        "privacy_status": "unlisted",
    }
    video_bytes = b"video-bytes"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url}")
        if str(request.url) == post.video_url:
            return httpx.Response(200, content=video_bytes, headers={"content-type": "video/mp4"})
        if request.url.path == "/upload/youtube/v3/videos":
            payload = request.read().decode()
            assert '"title":"Осенняя коллекция"' in payload
            assert '"privacyStatus":"unlisted"' in payload
            assert request.headers["x-upload-content-length"] == str(len(video_bytes))
            return httpx.Response(
                200, headers={"location": "https://upload.youtube.test/session-1"}
            )
        if str(request.url) == "https://upload.youtube.test/session-1":
            assert request.read() == video_bytes
            return httpx.Response(200, json={"id": "yt-video-1"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    settings = SimpleNamespace(r2_public_url_base="https://media.cindra.test")
    with (
        _client(handler) as client,
        patch("app.social_integrations.media_validation.get_settings", return_value=settings),
    ):
        result = youtube.publish(account, post, client)

    assert result == {"id": "yt-video-1"}
    assert calls[-1] == "PUT https://upload.youtube.test/session-1"


def test_publish_falls_back_to_post_text_when_no_title_given() -> None:
    account, post = _account_and_post({})

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == post.video_url:
            return httpx.Response(200, content=b"x", headers={"content-type": "video/mp4"})
        if request.url.path == "/upload/youtube/v3/videos":
            payload = request.read().decode()
            assert '"title":"Осенняя коллекция уже здесь"' in payload
            return httpx.Response(
                200, headers={"location": "https://upload.youtube.test/session-2"}
            )
        return httpx.Response(200, json={"id": "yt-video-2"})

    settings = SimpleNamespace(r2_public_url_base="https://media.cindra.test")
    with (
        _client(handler) as client,
        patch("app.social_integrations.media_validation.get_settings", return_value=settings),
    ):
        youtube.publish(account, post, client)


def test_init_resumable_upload_requires_location_header() -> None:
    account, post = _account_and_post({})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)  # no Location header

    with (
        _client(handler) as client,
        pytest.raises(PermanentPublishError, match="адрес"),
    ):
        youtube._init_resumable_upload(
            "access-token", account, post, 3, "video/mp4", client
        )


def test_ensure_fresh_access_token_refreshes_when_expiring() -> None:
    account = SocialAccount(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        platform=SocialPlatform.youtube,
        external_account_id="UC123",
        encrypted_access_token=encrypt_token("stale-access"),
        encrypted_refresh_token=encrypt_token("refresh-token"),
        token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = parse_qs(request.content.decode())
        assert body["refresh_token"] == ["refresh-token"]
        return httpx.Response(200, json={"access_token": "fresh-access", "expires_in": 3600})

    settings = SimpleNamespace(youtube_client_id="client-id", youtube_client_secret="secret")
    with (
        _client(handler) as client,
        patch("app.social_integrations.youtube.get_settings", return_value=settings),
    ):
        token = youtube.ensure_fresh_access_token(account, client)

    assert token == "fresh-access"


def test_ensure_fresh_access_token_without_a_refresh_token_raises() -> None:
    account = SocialAccount(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        platform=SocialPlatform.youtube,
        external_account_id="UC123",
        encrypted_access_token=encrypt_token("stale-access"),
        encrypted_refresh_token=None,
        token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    with pytest.raises(PermanentPublishError, match="заново"):
        youtube.ensure_fresh_access_token(account)
