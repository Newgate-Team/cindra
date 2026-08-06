from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models import Post, SocialPlatform, User
from app.social_accounts import upsert_social_account
from app.social_integrations import facebook
from app.social_integrations.errors import PermanentPublishError, TransientPublishError


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_publish_text_returns_result_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v21.0/page-1/feed"
        assert "message=" in str(request.url)
        return httpx.Response(200, json={"id": "page-1_111"})

    result = facebook.publish_text("page-1", "Привет!", "page-token", client=_client(handler))
    assert result == {"id": "page-1_111"}


def test_publish_photo_returns_result_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v21.0/page-1/photos"
        assert "url=" in str(request.url)
        assert "caption=" in str(request.url)
        return httpx.Response(200, json={"id": "photo-1", "post_id": "page-1_222"})

    result = facebook.publish_photo(
        "page-1", "https://example.com/x.jpg", "caption", "page-token", client=_client(handler)
    )
    assert result == {"id": "photo-1", "post_id": "page-1_222"}


def test_publish_photo_strips_markdown_from_caption() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["caption"] = dict(request.url.params)["caption"]
        return httpx.Response(200, json={"id": "photo-1"})

    facebook.publish_photo(
        "page-1", "https://example.com/x.jpg", "**жирный** текст", "page-token", client=_client(handler)
    )
    assert captured["caption"] == "жирный текст"


def test_publish_video_strips_markdown_from_description() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["description"] = dict(request.url.params)["description"]
        return httpx.Response(200, json={"id": "video-1"})

    facebook.publish_video(
        "page-1", "https://example.com/x.mp4", "**жирный** текст", "page-token", client=_client(handler)
    )
    assert captured["description"] == "жирный текст"


def test_publish_text_strips_markdown() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["message"] = dict(request.url.params)["message"]
        return httpx.Response(200, json={"id": "page-1_111"})

    facebook.publish_text("page-1", "**Осенний** латте", "page-token", client=_client(handler))
    assert captured["message"] == "Осенний латте"


def test_permanent_error_on_bad_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "Invalid OAuth access token"}})

    with pytest.raises(PermanentPublishError):
        facebook.publish_text("page-1", "Привет!", "bad-token", client=_client(handler))


def test_rate_limit_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    with pytest.raises(TransientPublishError):
        facebook.publish_text("page-1", "Привет!", "token", client=_client(handler))


def test_publish_video_returns_result_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v21.0/page-1/videos"
        assert "file_url=" in str(request.url)
        assert "description=" in str(request.url)
        return httpx.Response(200, json={"id": "video-1"})

    result = facebook.publish_video(
        "page-1", "https://example.com/x.mp4", "caption", "page-token", client=_client(handler)
    )
    assert result == {"id": "video-1"}


def test_publish_without_image_url_posts_to_feed(db: Session, user: User) -> None:
    account = upsert_social_account(
        db, user, SocialPlatform.facebook, "page-1", access_token="page-token"
    )
    post = Post(
        user_id=user.id,
        social_account_id=account.id,
        text="без картинки",
        scheduled_for=datetime.now(UTC),
    )

    with patch(
        "app.social_integrations.facebook.publish_text", return_value={"id": "page-1_111"}
    ) as publish_text:
        result = facebook.publish(account, post)

    publish_text.assert_called_once_with("page-1", "без картинки", "page-token")
    assert result == {"id": "page-1_111"}


def test_publish_with_image_url_posts_to_photos(db: Session, user: User) -> None:
    account = upsert_social_account(
        db, user, SocialPlatform.facebook, "page-1", access_token="page-token"
    )
    post = Post(
        user_id=user.id,
        social_account_id=account.id,
        text="caption",
        image_url="https://example.com/x.jpg",
        scheduled_for=datetime.now(UTC),
    )

    with patch(
        "app.social_integrations.facebook.publish_photo", return_value={"id": "photo-1"}
    ) as publish_photo:
        result = facebook.publish(account, post)

    publish_photo.assert_called_once_with(
        "page-1", "https://example.com/x.jpg", "caption", "page-token"
    )
    assert result == {"id": "photo-1"}


def test_publish_with_video_url_posts_to_videos(db: Session, user: User) -> None:
    account = upsert_social_account(
        db, user, SocialPlatform.facebook, "page-1", access_token="page-token"
    )
    post = Post(
        user_id=user.id,
        social_account_id=account.id,
        text="caption",
        video_url="https://example.com/x.mp4",
        scheduled_for=datetime.now(UTC),
    )

    with patch(
        "app.social_integrations.facebook.publish_video", return_value={"id": "video-1"}
    ) as publish_video:
        result = facebook.publish(account, post)

    publish_video.assert_called_once_with(
        "page-1", "https://example.com/x.mp4", "caption", "page-token"
    )
    assert result == {"id": "video-1"}
