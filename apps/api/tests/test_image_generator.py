import json
from unittest.mock import patch

import httpx
import pytest

from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.image_generator import (
    ImageGenerationFailedError,
    nano_banana_image_generator,
)


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


def test_image_attachment_becomes_reference_image_in_input_array() -> None:
    reference_bytes = b"fake-reference-image"

    def handler(request: httpx.Request) -> httpx.Response:
        if "interactions" in str(request.url):
            body = json.loads(request.content)
            assert isinstance(body["input"], list)
            assert body["input"][0] == {"type": "text", "text": body["input"][0]["text"]}
            assert "утренний кофе" in body["input"][0]["text"]
            assert body["input"][1]["type"] == "image"
            assert body["input"][1]["mime_type"] == "image/jpeg"
            import base64

            assert base64.b64decode(body["input"][1]["data"]) == reference_bytes
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output_image": {"data": "ZmFrZS1pbWFnZQ==", "mime_type": "image/png"},
                },
            )
        return httpx.Response(200, content=reference_bytes)

    payload = {
        "topic": "утренний кофе",
        "platform": "instagram",
        "attachments": [{"url": "https://r2.example/mood.jpg", "attachment_type": "image"}],
    }
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.png",
    ):
        result = nano_banana_image_generator(payload, client=_client(handler))
    assert result["image_url"] == "https://media.cindra.example/abc.png"


def test_two_image_attachments_both_become_reference_images() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "interactions" in str(request.url):
            body = json.loads(request.content)
            assert isinstance(body["input"], list)
            assert len(body["input"]) == 3  # text + 2 reference images
            assert [p["type"] for p in body["input"]] == ["text", "image", "image"]
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output_image": {"data": "ZmFrZS1pbWFnZQ==", "mime_type": "image/png"},
                },
            )
        return httpx.Response(200, content=b"fake-reference-image")

    payload = {
        "topic": "утренний кофе",
        "platform": "instagram",
        "attachments": [
            {"url": "https://r2.example/mood1.jpg", "attachment_type": "image"},
            {"url": "https://r2.example/mood2.jpg", "attachment_type": "image"},
        ],
    }
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.png",
    ):
        nano_banana_image_generator(payload, client=_client(handler))


def test_document_attachment_adds_context_without_changing_input_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "interactions" in str(request.url):
            body = json.loads(request.content)
            assert isinstance(body["input"], str)
            assert "бренд-гайд от клиента" in body["input"]
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output_image": {"data": "ZmFrZS1pbWFnZQ==", "mime_type": "image/png"},
                },
            )
        return httpx.Response(200, content="бренд-гайд от клиента".encode())

    payload = {
        "topic": "утренний кофе",
        "platform": "instagram",
        "attachments": [{"url": "https://r2.example/brief.txt", "attachment_type": "document"}],
    }
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.png",
    ):
        nano_banana_image_generator(payload, client=_client(handler))


def test_400_is_not_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})

    with pytest.raises(httpx.HTTPStatusError):
        nano_banana_image_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler)
        )


def test_missing_output_image_raises_clear_error_not_keyerror() -> None:
    # Gemini can respond 200 with no output_image at all -- e.g. it
    # declined to generate an image for this request. Blindly indexing
    # body["output_image"] used to crash with a bare, unhelpful
    # KeyError("output_image") that got shown to the end user verbatim.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "abc123", "status": "completed"})

    with pytest.raises(ImageGenerationFailedError) as exc_info:
        nano_banana_image_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler)
        )
    assert "output_image" not in str(exc_info.value)


def test_missing_output_image_logs_raw_response_for_diagnosis(caplog: pytest.LogCaptureFixture) -> None:
    # CIN-110: the user-facing error stays generic, but the raw response
    # body is logged so a real failure can be diagnosed after the fact
    # instead of guessed at (see CIN-105's unresolved root cause).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"id": "abc123", "status": "completed", "steps": [{"type": "text"}]}
        )

    with caplog.at_level("WARNING"), pytest.raises(ImageGenerationFailedError):
        nano_banana_image_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler)
        )

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "status=completed" in message
    assert '"steps"' in message
