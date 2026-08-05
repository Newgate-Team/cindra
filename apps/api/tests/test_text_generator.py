import json

import httpx
import pytest

from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.text_generator import gemini_text_generator


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sends_correct_request_shape_and_parses_response() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url).split("?")[0]
        captured["params"] = dict(request.url.params)
        captured["body"] = request.content
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "Готовый пост про кофе"}]}}
                ]
            },
        )

    payload = {"topic": "утренний кофе", "platform": "telegram"}
    result = gemini_text_generator(payload, client=_client(handler))

    assert result["text"] == "Готовый пост про кофе"
    assert "утренний кофе" in result["prompt"]
    assert captured["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.5-flash-lite:generateContent"
    )
    assert "key" in captured["params"]
    body = json.loads(captured["body"])
    assert "утренний кофе" in body["contents"][0]["parts"][0]["text"]


def test_429_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}})

    with pytest.raises(TransientGenerationError):
        gemini_text_generator(
            {"topic": "x", "platform": "telegram"}, client=_client(handler)
        )


def test_5xx_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})

    with pytest.raises(TransientGenerationError):
        gemini_text_generator(
            {"topic": "x", "platform": "telegram"}, client=_client(handler)
        )


def test_network_timeout_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("The read operation timed out", request=request)

    with pytest.raises(TransientGenerationError):
        gemini_text_generator(
            {"topic": "x", "platform": "telegram"}, client=_client(handler)
        )


def test_400_is_not_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})

    with pytest.raises(httpx.HTTPStatusError):
        gemini_text_generator(
            {"topic": "x", "platform": "telegram"}, client=_client(handler)
        )
