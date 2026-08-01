import json

import httpx
import pytest

from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.image_generator import imagen_image_generator


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
            json={"predictions": [{"bytesBase64Encoded": "ZmFrZS1pbWFnZQ=="}]},
        )

    payload = {"topic": "утренний кофе", "platform": "instagram"}
    result = imagen_image_generator(payload, client=_client(handler))

    assert result["image_base64"] == "ZmFrZS1pbWFnZQ=="
    assert "утренний кофе" in result["prompt"]
    assert captured["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "imagen-4.0-generate-001:predict"
    )
    assert "key" in captured["params"]
    body = json.loads(captured["body"])
    assert "утренний кофе" in body["instances"][0]["prompt"]


def test_429_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}})

    with pytest.raises(TransientGenerationError):
        imagen_image_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler)
        )


def test_5xx_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})

    with pytest.raises(TransientGenerationError):
        imagen_image_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler)
        )


def test_400_is_not_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})

    with pytest.raises(httpx.HTTPStatusError):
        imagen_image_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler)
        )
