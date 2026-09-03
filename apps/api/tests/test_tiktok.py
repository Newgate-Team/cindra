import io
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx
import pytest

from app.models import Post, SocialAccount, SocialPlatform
from app.social_integrations import tiktok
from app.social_integrations.errors import PermanentPublishError
from app.token_crypto import encrypt_token


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_exchange_code_for_token_uses_form_encoded_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == tiktok._TOKEN_URL
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["authorization_code"]
        assert body["code"] == ["auth-code"]
        assert body["client_key"] == ["client-key"]
        return httpx.Response(
            200,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 86400,
                "open_id": "open-id",
                "scope": "user.info.basic,video.publish",
            },
        )

    with _client(handler) as client:
        result = tiktok.exchange_code_for_token(
            "auth-code", "client-key", "client-secret", "https://example.test/callback", client
        )
    assert result["open_id"] == "open-id"


def test_query_creator_info_rejects_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {},
                "error": {"code": "access_token_invalid", "message": "expired"},
            },
        )

    with _client(handler) as client, pytest.raises(PermanentPublishError, match="expired"):
        tiktok.query_creator_info("bad-token", client)


def test_direct_post_queries_creator_then_uploads_video() -> None:
    account_id = uuid.uuid4()
    account = SocialAccount(
        id=account_id,
        user_id=uuid.uuid4(),
        platform=SocialPlatform.tiktok,
        external_account_id="open-id",
        encrypted_access_token=encrypt_token("access-token"),
        encrypted_refresh_token=encrypt_token("refresh-token"),
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    post = Post(
        id=uuid.uuid4(),
        user_id=account.user_id,
        social_account_id=account.id,
        text="Новый продукт #newgate",
        video_url="https://media.cindra.test/video.mp4",
        content_kind="post",
        platform_options={
            "tiktok": {
                "accounts": {
                    str(account.id): {
                        "mode": "direct_post",
                        "privacy_level": "SELF_ONLY",
                        "disable_comment": False,
                        "disable_duet": True,
                        "disable_stitch": False,
                        "brand_content_toggle": False,
                        "brand_organic_toggle": True,
                        "is_aigc": True,
                    }
                }
            }
        },
        scheduled_for=datetime.now(UTC),
    )
    video_bytes = b"video-bytes"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url}")
        if request.url == tiktok._CREATOR_INFO_URL:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "creator_username": "newgate_team",
                        "creator_nickname": "Newgate Team",
                        "privacy_level_options": ["SELF_ONLY"],
                        "comment_disabled": False,
                        "duet_disabled": False,
                        "stitch_disabled": False,
                        "max_video_post_duration_sec": 60,
                    },
                    "error": {"code": "ok", "message": ""},
                },
            )
        if str(request.url) == post.video_url:
            return httpx.Response(200, content=video_bytes, headers={"content-type": "video/mp4"})
        if request.url == tiktok._DIRECT_POST_URL:
            payload = request.read().decode()
            assert '"privacy_level":"SELF_ONLY"' in payload
            assert '"is_aigc":true' in payload
            return httpx.Response(
                200,
                json={
                    "data": {
                        "publish_id": "publish-123",
                        "upload_url": "https://upload.tiktok.test/video",
                    },
                    "error": {"code": "ok", "message": ""},
                },
            )
        if str(request.url) == "https://upload.tiktok.test/video":
            assert request.headers["content-range"] == f"bytes 0-{len(video_bytes) - 1}/{len(video_bytes)}"
            assert request.read() == video_bytes
            return httpx.Response(201)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    settings = SimpleNamespace(
        r2_public_url_base="https://media.cindra.test",
        tiktok_client_key="client-key",
        tiktok_client_secret="client-secret",
    )
    with (
        _client(handler) as client,
        patch("app.social_integrations.tiktok.get_settings", return_value=settings),
        patch("app.social_integrations.media_validation.get_settings", return_value=settings),
    ):
        result = tiktok.publish(account, post, client)

    assert result == {"id": "publish-123", "mode": "direct_post"}
    assert calls[-1] == "PUT https://upload.tiktok.test/video"


def test_draft_upload_skips_creator_settings_and_sends_video_to_inbox() -> None:
    account_id = uuid.uuid4()
    account = SocialAccount(
        id=account_id,
        user_id=uuid.uuid4(),
        platform=SocialPlatform.tiktok,
        external_account_id="open-id",
        encrypted_access_token=encrypt_token("access-token"),
        encrypted_refresh_token=encrypt_token("refresh-token"),
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    post = Post(
        id=uuid.uuid4(),
        user_id=account.user_id,
        social_account_id=account.id,
        text="Черновик для TikTok",
        video_url="https://media.cindra.test/draft.mp4",
        content_kind="post",
        platform_options={
            "tiktok": {"accounts": {str(account.id): {"mode": "draft_upload"}}}
        },
        scheduled_for=datetime.now(UTC),
    )
    video_bytes = b"draft-video"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url}")
        assert request.url != tiktok._CREATOR_INFO_URL
        if str(request.url) == post.video_url:
            return httpx.Response(200, content=video_bytes, headers={"content-type": "video/mp4"})
        if request.url == tiktok._DRAFT_UPLOAD_URL:
            payload = request.read().decode()
            assert '"source":"FILE_UPLOAD"' in payload
            assert '"post_info"' not in payload
            return httpx.Response(
                200,
                json={
                    "data": {
                        "publish_id": "draft-123",
                        "upload_url": "https://upload.tiktok.test/draft",
                    },
                    "error": {"code": "ok", "message": ""},
                },
            )
        if str(request.url) == "https://upload.tiktok.test/draft":
            assert request.headers["content-range"] == (
                f"bytes 0-{len(video_bytes) - 1}/{len(video_bytes)}"
            )
            assert request.read() == video_bytes
            return httpx.Response(201)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    settings = SimpleNamespace(r2_public_url_base="https://media.cindra.test")
    with (
        _client(handler) as client,
        patch("app.social_integrations.tiktok.get_settings", return_value=settings),
        patch("app.social_integrations.media_validation.get_settings", return_value=settings),
    ):
        result = tiktok.publish(account, post, client)

    assert result == {"id": "draft-123", "mode": "draft_upload"}
    assert calls == [
        "GET https://media.cindra.test/draft.mp4",
        f"POST {tiktok._DRAFT_UPLOAD_URL}",
        "PUT https://upload.tiktok.test/draft",
    ]


def test_direct_post_requires_creator_selected_privacy() -> None:
    account = SocialAccount(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        platform=SocialPlatform.tiktok,
        external_account_id="open-id",
        encrypted_access_token=encrypt_token("access-token"),
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    post = Post(
        id=uuid.uuid4(),
        user_id=account.user_id,
        social_account_id=account.id,
        text="Caption",
        video_url="https://media.cindra.test/video.mp4",
        platform_options={"tiktok": {"accounts": {str(account.id): {}}}},
        scheduled_for=datetime.now(UTC),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == tiktok._CREATOR_INFO_URL:
            return httpx.Response(
                200,
                json={
                    "data": {"privacy_level_options": ["SELF_ONLY"]},
                    "error": {"code": "ok", "message": ""},
                },
            )
        if str(request.url) == post.video_url:
            return httpx.Response(200, content=b"video", headers={"content-type": "video/mp4"})
        raise AssertionError("Direct Post init must not run without privacy")

    settings = SimpleNamespace(r2_public_url_base="https://media.cindra.test")
    with (
        _client(handler) as client,
        patch("app.social_integrations.tiktok.get_settings", return_value=settings),
        patch("app.social_integrations.media_validation.get_settings", return_value=settings),
        pytest.raises(PermanentPublishError, match="приватности"),
    ):
        tiktok.publish(account, post, client)


def test_download_video_rejects_url_outside_r2_bucket() -> None:
    # CIN-156: _download_video is our worker fetching a client-supplied
    # URL server-side for FILE_UPLOAD -- without this guard it's an SSRF
    # proxy, same class of bug closed here for Telegram's send_video.
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not fetch an off-bucket URL")

    settings = SimpleNamespace(r2_public_url_base="https://media.cindra.test")
    with (
        _client(handler) as client,
        patch("app.social_integrations.media_validation.get_settings", return_value=settings),
        pytest.raises(PermanentPublishError, match="TikTok"),
    ):
        tiktok._download_video("https://evil.example.com/video.mp4", io.BytesIO(), client)


def test_download_video_accepts_r2_bucket_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://media.cindra.test/video.mp4"
        return httpx.Response(200, content=b"video-bytes", headers={"content-type": "video/mp4"})

    settings = SimpleNamespace(r2_public_url_base="https://media.cindra.test")
    with (
        _client(handler) as client,
        patch("app.social_integrations.media_validation.get_settings", return_value=settings),
    ):
        size, content_type = tiktok._download_video(
            "https://media.cindra.test/video.mp4", io.BytesIO(), client
        )

    assert size == len(b"video-bytes")
    assert content_type == "video/mp4"
