import httpx
import pytest

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


def test_send_message_permanent_error_when_bot_not_admin() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"ok": False, "error_code": 400, "description": "Bad Request: have no rights to send a message"},
        )

    with pytest.raises(PermanentPublishError):
        send_message("-100123", "Привет!", "123:abc", client=_client(handler))
