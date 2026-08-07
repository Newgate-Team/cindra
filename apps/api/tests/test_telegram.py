from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models import Post, SocialPlatform, User
from app.social_accounts import upsert_social_account
from app.social_integrations import telegram
from app.social_integrations.errors import PermanentPublishError, TransientPublishError
from app.social_integrations.telegram import (
    get_chat,
    get_chat_member,
    get_me,
    send_message,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_get_chat_returns_result_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bot123:abc/getChat"
        assert "chat_id=%40mychannel" in str(request.url)
        return httpx.Response(200, json={"ok": True, "result": {"id": -100123, "title": "My Channel"}})

    result = get_chat("@mychannel", "123:abc", client=_client(handler))
    assert result == {"id": -100123, "title": "My Channel"}


def test_get_chat_permanent_error_on_unauthorized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})

    with pytest.raises(PermanentPublishError):
        get_chat("@mychannel", "bad-token", client=_client(handler))


def test_get_chat_transient_error_on_rate_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"ok": False, "error_code": 429, "description": "Too Many Requests"}
        )

    with pytest.raises(TransientPublishError):
        get_chat("@mychannel", "123:abc", client=_client(handler))


def test_get_me_returns_bot_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bot123:abc/getMe"
        return httpx.Response(200, json={"ok": True, "result": {"id": 999, "username": "cindra_bot"}})

    result = get_me("123:abc", client=_client(handler))
    assert result == {"id": 999, "username": "cindra_bot"}


def test_get_chat_member_returns_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bot123:abc/getChatMember"
        assert "user_id=999" in str(request.url)
        return httpx.Response(200, json={"ok": True, "result": {"status": "member"}})

    result = get_chat_member("@mychannel", 999, "123:abc", client=_client(handler))
    assert result == {"status": "member"}


def test_get_chat_member_permanent_error_when_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"ok": False, "error_code": 400, "description": "Bad Request: user not found"}
        )

    with pytest.raises(PermanentPublishError):
        get_chat_member("@mychannel", 999, "123:abc", client=_client(handler))


def test_send_message_returns_result_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bot123:abc/sendMessage"
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    result = send_message("-100123", "Привет!", "123:abc", client=_client(handler))
    assert result == {"message_id": 42}


def test_send_message_converts_markdown_and_sets_parse_mode() -> None:
    import json

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    send_message("-100123", "**Осенний латте** уже в продаже!", "123:abc", client=_client(handler))
    assert captured["body"]["parse_mode"] == "MarkdownV2"
    assert captured["body"]["text"] == "*Осенний латте* уже в продаже\\!"


def test_send_message_permanent_error_when_bot_not_admin() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"ok": False, "error_code": 400, "description": "Bad Request: have no rights to send a message"},
        )

    with pytest.raises(PermanentPublishError):
        send_message("-100123", "Привет!", "123:abc", client=_client(handler))


def test_send_video_returns_result_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bot123:abc/sendVideo":
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 43}})
        return httpx.Response(200, content=b"fake-video-bytes")  # the video_url fetch

    result = telegram.send_video(
        "-100123", "https://example.com/x.mp4", "caption", "123:abc", client=_client(handler)
    )
    assert result == {"message_id": 43}


def test_send_video_uploads_bytes_as_multipart_not_url() -> None:
    # CIN-115: Telegram's own URL-based fetch caps at 20MB and rejects
    # a real generated video ("Bad Request: failed to get HTTP URL
    # content") -- send_video downloads the bytes itself and uploads
    # them as multipart/form-data instead, which allows up to 50MB.
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bot123:abc/sendVideo":
            captured["content_type"] = request.headers.get("content-type", "")
            captured["body"] = request.content
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 43}})
        return httpx.Response(200, content=b"fake-video-bytes")

    telegram.send_video(
        "-100123", "https://example.com/x.mp4", "caption", "123:abc", client=_client(handler)
    )
    assert "multipart/form-data" in captured["content_type"]
    assert b"fake-video-bytes" in captured["body"]
    assert b"https://example.com/x.mp4" not in captured["body"]


def test_send_photo_converts_markdown_and_sets_parse_mode() -> None:
    import json

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 45}})

    telegram.send_photo(
        "-100123", "https://example.com/x.jpg", "**жирная** подпись", "123:abc", client=_client(handler)
    )
    assert captured["body"]["parse_mode"] == "MarkdownV2"
    assert captured["body"]["caption"] == "*жирная* подпись"


def test_send_video_converts_markdown_and_sets_parse_mode() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bot123:abc/sendVideo":
            captured["body"] = request.content.decode("utf-8")
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 46}})
        return httpx.Response(200, content=b"fake-video-bytes")

    telegram.send_video(
        "-100123", "https://example.com/x.mp4", "**жирная** подпись", "123:abc", client=_client(handler)
    )
    # multipart/form-data fields are embedded as raw text (not JSON),
    # so this checks for the encoded field values directly.
    assert "MarkdownV2" in captured["body"]
    assert "*жирная* подпись" in captured["body"]


def test_publish_with_video_url_sends_video(db: Session, user: User) -> None:
    account = upsert_social_account(
        db, user, SocialPlatform.telegram, "-100123", access_token="123:abc"
    )
    post = Post(
        user_id=user.id,
        social_account_id=account.id,
        text="caption",
        video_url="https://example.com/x.mp4",
        scheduled_for=datetime.now(UTC),
    )

    with patch(
        "app.social_integrations.telegram.send_video", return_value={"message_id": 44}
    ) as send_video_mock:
        result = telegram.publish(account, post)

    send_video_mock.assert_called_once_with(
        "-100123", "https://example.com/x.mp4", "caption", "123:abc"
    )
    assert result == {"message_id": 44}
