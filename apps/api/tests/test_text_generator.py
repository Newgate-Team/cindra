import json

import httpx
import pytest

from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.text_generator import anthropic_text_generator


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sends_correct_request_shape_and_parses_response() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["body"] = request.content
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "Готовый пост про кофе"}]},
        )

    payload = {"topic": "утренний кофе", "platform": "telegram"}
    result = anthropic_text_generator(payload, client=_client(handler))

    assert result["text"] == "Готовый пост про кофе"
    assert "утренний кофе" in result["prompt"]
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert "x-api-key" in captured["headers"]
    body = json.loads(captured["body"])
    assert body["model"]
    assert "утренний кофе" in body["messages"][0]["content"]


def test_429_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    with pytest.raises(TransientGenerationError):
        anthropic_text_generator(
            {"topic": "x", "platform": "telegram"}, client=_client(handler)
        )


def test_5xx_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "overloaded"})

    with pytest.raises(TransientGenerationError):
        anthropic_text_generator(
            {"topic": "x", "platform": "telegram"}, client=_client(handler)
        )


def test_401_is_not_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "authentication_error"})

    with pytest.raises(httpx.HTTPStatusError):
        anthropic_text_generator(
            {"topic": "x", "platform": "telegram"}, client=_client(handler)
        )
