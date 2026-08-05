import json
from unittest.mock import patch

import httpx
import pytest

from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.image_generator import nano_banana_image_generator


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
            json={
                "id": "abc123",
                "status": "completed",
                "output_image": {
                    "type": "image",
                    "data": "ZmFrZS1pbWFnZQ==",
                    "mime_type": "image/png",
                },
            },
        )

    payload = {"topic": "утренний кофе", "platform": "instagram"}
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.png",
    ) as upload:
        result = nano_banana_image_generator(payload, client=_client(handler))

    upload.assert_called_once_with(b"fake-image", "image/png", "png")
    assert result["image_url"] == "https://media.cindra.example/abc.png"
    assert "утренний кофе" in result["prompt"]
    assert captured["url"] == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert "x-goog-api-key" in captured["headers"]
    body = json.loads(captured["body"])
    assert body["model"] == "gemini-2.5-flash-image"
    assert "утренний кофе" in body["input"]


def test_429_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}})

    with pytest.raises(TransientGenerationError):
        nano_banana_image_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler)
        )


def test_5xx_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})

    with pytest.raises(TransientGenerationError):
        nano_banana_image_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler)
        )


def test_network_timeout_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("The read operation timed out", request=request)

    with pytest.raises(TransientGenerationError):
        nano_banana_image_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler)
        )


def test_400_is_not_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})

    with pytest.raises(httpx.HTTPStatusError):
        nano_banana_image_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler)
        )
