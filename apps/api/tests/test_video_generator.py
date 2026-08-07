import json
from unittest.mock import patch

import httpx
import pytest

from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.video_generator import (
    VideoGenerationFailedError,
    veo_video_generator,
)

_VIDEO_URI = "https://generativelanguage.googleapis.com/v1beta/files/xyz"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _no_sleep(_: float) -> None:
    pass


def test_starts_operation_polls_downloads_and_returns_video_url() -> None:
    captured = {"requests": []}
    poll_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        captured["requests"].append((request.method, url))
        if request.method == "POST":
            captured["start_body"] = request.content
            return httpx.Response(200, json={"name": "operations/abc123"})

        if url == _VIDEO_URI:
            assert request.headers.get("x-goog-api-key") is not None
            return httpx.Response(200, content=b"fake-video-bytes")

        poll_count["n"] += 1
        if poll_count["n"] < 2:
            return httpx.Response(200, json={"name": "operations/abc123", "done": False})
        return httpx.Response(
            200,
            json={
                "name": "operations/abc123",
                "done": True,
                "response": {
                    "generateVideoResponse": {
                        "generatedSamples": [{"video": {"uri": _VIDEO_URI}}]
                    }
                },
            },
        )

    payload = {"topic": "утренний кофе", "platform": "instagram"}
    with patch(
        "app.content_pipeline.video_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.mp4",
    ) as upload:
        result = veo_video_generator(payload, client=_client(handler), sleep=_no_sleep)

    upload.assert_called_once_with(b"fake-video-bytes", "video/mp4", "mp4")
    assert result["video_url"] == "https://media.cindra.example/abc.mp4"
    assert "утренний кофе" in result["prompt"]
    start_url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "veo-3.1-fast-generate-preview:predictLongRunning"
    )
    assert captured["requests"][0] == ("POST", start_url)
    assert captured["requests"][1] == (
        "GET",
        "https://generativelanguage.googleapis.com/v1beta/operations/abc123",
    )
    body = json.loads(captured["start_body"])
    assert "утренний кофе" in body["instances"][0]["prompt"]
    assert body["parameters"]["durationSeconds"] == "8"
    assert body["parameters"]["resolution"] == "1080p"


def test_start_429_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}})

    with pytest.raises(TransientGenerationError):
        veo_video_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler), sleep=_no_sleep
        )


def test_start_network_timeout_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("The read operation timed out", request=request)

    with pytest.raises(TransientGenerationError):
        veo_video_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler), sleep=_no_sleep
        )


def test_start_400_is_not_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})

    with pytest.raises(httpx.HTTPStatusError):
        veo_video_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler), sleep=_no_sleep
        )


def test_poll_5xx_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"name": "operations/abc123"})
        return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})

    with pytest.raises(TransientGenerationError):
        veo_video_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler), sleep=_no_sleep
        )


def test_operation_error_is_not_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"name": "operations/abc123"})
        return httpx.Response(
            200,
            json={"name": "operations/abc123", "done": True, "error": {"message": "boom"}},
        )

    with pytest.raises(VideoGenerationFailedError):
        veo_video_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler), sleep=_no_sleep
        )


def test_never_finishing_operation_raises_after_poll_budget() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"name": "operations/abc123"})
        return httpx.Response(200, json={"name": "operations/abc123", "done": False})

    with pytest.raises(VideoGenerationFailedError):
        veo_video_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler), sleep=_no_sleep
        )


def test_document_attachment_adds_context_to_prompt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == "https://r2.example/brief.txt":
            return httpx.Response(200, content="сценарий из ТЗ клиента".encode())
        if request.method == "POST":
            body = json.loads(request.content)
            assert "сценарий из ТЗ клиента" in body["instances"][0]["prompt"]
            return httpx.Response(200, json={"name": "operations/abc123"})
        return httpx.Response(
            200,
            json={
                "name": "operations/abc123",
                "done": True,
                "response": {
                    "generateVideoResponse": {"generatedSamples": [{"video": {"uri": _VIDEO_URI}}]}
                },
            },
        )

    payload = {
        "topic": "x",
        "platform": "instagram",
        "attachments": [{"url": "https://r2.example/brief.txt", "attachment_type": "document"}],
    }
    with patch(
        "app.content_pipeline.video_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.mp4",
    ):
        veo_video_generator(payload, client=_client(handler), sleep=_no_sleep)


def test_image_attachment_is_not_used() -> None:
    # Veo has no documented way to take arbitrary image/video/audio
    # context (only text-to-video) -- an image attachment shouldn't
    # even trigger a fetch, let alone change the prompt.
    fetched = {"called": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://r2.example"):
            fetched["called"] = True
            return httpx.Response(200, content=b"unused")
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["instances"][0]["prompt"] == "Короткое видео на тему: x."
            return httpx.Response(200, json={"name": "operations/abc123"})
        return httpx.Response(
            200,
            json={
                "name": "operations/abc123",
                "done": True,
                "response": {
                    "generateVideoResponse": {"generatedSamples": [{"video": {"uri": _VIDEO_URI}}]}
                },
            },
        )

    payload = {
        "topic": "x",
        "platform": "instagram",
        "attachments": [{"url": "https://r2.example/mood.jpg", "attachment_type": "image"}],
    }
    with patch(
        "app.content_pipeline.video_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.mp4",
    ):
        veo_video_generator(payload, client=_client(handler), sleep=_no_sleep)
    assert fetched["called"] is False


def test_two_document_attachments_are_both_included() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == "https://r2.example/brief.txt":
            return httpx.Response(200, content="сценарий из ТЗ клиента".encode())
        if url == "https://r2.example/notes.txt":
            return httpx.Response(200, content="дополнительные заметки".encode())
        if request.method == "POST":
            body = json.loads(request.content)
            prompt = body["instances"][0]["prompt"]
            assert "сценарий из ТЗ клиента" in prompt
            assert "дополнительные заметки" in prompt
            return httpx.Response(200, json={"name": "operations/abc123"})
        return httpx.Response(
            200,
            json={
                "name": "operations/abc123",
                "done": True,
                "response": {
                    "generateVideoResponse": {"generatedSamples": [{"video": {"uri": _VIDEO_URI}}]}
                },
            },
        )

    payload = {
        "topic": "x",
        "platform": "instagram",
        "attachments": [
            {"url": "https://r2.example/brief.txt", "attachment_type": "document"},
            {"url": "https://r2.example/notes.txt", "attachment_type": "document"},
        ],
    }
    with patch(
        "app.content_pipeline.video_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.mp4",
    ):
        veo_video_generator(payload, client=_client(handler), sleep=_no_sleep)
