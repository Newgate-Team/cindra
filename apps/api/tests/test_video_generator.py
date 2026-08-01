import json

import httpx
import pytest

from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.video_generator import (
    VideoGenerationFailedError,
    veo_video_generator,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _no_sleep(_: float) -> None:
    pass


def test_starts_operation_polls_and_returns_video_uri() -> None:
    captured = {"requests": []}
    poll_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["requests"].append((request.method, str(request.url).split("?")[0]))
        if request.method == "POST":
            captured["start_body"] = request.content
            return httpx.Response(200, json={"name": "operations/abc123"})

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
                        "generatedSamples": [
                            {"video": {"uri": "https://generativelanguage.googleapis.com/v1beta/files/xyz"}}
                        ]
                    }
                },
            },
        )

    payload = {"topic": "утренний кофе", "platform": "instagram"}
    result = veo_video_generator(payload, client=_client(handler), sleep=_no_sleep)

    assert result["video_uri"] == "https://generativelanguage.googleapis.com/v1beta/files/xyz"
    assert "утренний кофе" in result["prompt"]
    start_url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "veo-3.0-fast-generate-001:predictLongRunning"
    )
    assert captured["requests"][0] == ("POST", start_url)
    assert captured["requests"][1] == (
        "GET",
        "https://generativelanguage.googleapis.com/v1beta/operations/abc123",
    )
    body = json.loads(captured["start_body"])
    assert "утренний кофе" in body["instances"][0]["prompt"]


def test_start_429_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}})

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
