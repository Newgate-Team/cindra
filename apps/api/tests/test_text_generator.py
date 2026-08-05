import base64
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


def test_document_attachment_adds_context_to_prompt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "generativelanguage" in str(request.url):
            body = json.loads(request.content)
            assert "план запуска осенней коллекции" in body["contents"][0]["parts"][0]["text"]
            assert len(body["contents"][0]["parts"]) == 1  # no extra multimodal part
            return httpx.Response(
                200, json={"candidates": [{"content": {"parts": [{"text": "Готово"}]}}]}
            )
        return httpx.Response(200, content="план запуска осенней коллекции".encode())

    payload = {
        "topic": "осенняя коллекция",
        "platform": "telegram",
        "attachment_url": "https://r2.example/brief.txt",
        "attachment_type": "document",
    }
    result = gemini_text_generator(payload, client=_client(handler))
    assert result["text"] == "Готово"


def test_image_attachment_becomes_multimodal_part() -> None:
    image_bytes = b"fake-image-bytes"

    def handler(request: httpx.Request) -> httpx.Response:
        if "generativelanguage" in str(request.url):
            body = json.loads(request.content)
            parts = body["contents"][0]["parts"]
            assert len(parts) == 2
            assert parts[1]["inline_data"]["mime_type"] == "image/png"
            assert base64.b64decode(parts[1]["inline_data"]["data"]) == image_bytes
            return httpx.Response(
                200, json={"candidates": [{"content": {"parts": [{"text": "Готово"}]}}]}
            )
        return httpx.Response(200, content=image_bytes)

    payload = {
        "topic": "осенняя коллекция",
        "platform": "telegram",
        "attachment_url": "https://r2.example/reference.png",
        "attachment_type": "image",
    }
    result = gemini_text_generator(payload, client=_client(handler))
    assert result["text"] == "Готово"


def test_sets_max_output_tokens() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "x"}]}}]}
        )

    gemini_text_generator({"topic": "x", "platform": "telegram"}, client=_client(handler))
    assert captured["body"]["generationConfig"] == {"maxOutputTokens": 2048}


def test_400_is_not_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})

    with pytest.raises(httpx.HTTPStatusError):
        gemini_text_generator(
            {"topic": "x", "platform": "telegram"}, client=_client(handler)
        )
