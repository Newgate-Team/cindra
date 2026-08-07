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

        # CIN-114: a caption is generated via a separate generateContent
        # call after the video succeeds -- must not be confused with
        # the video's own predictLongRunning POST below.
        if "generateContent" in url:
            return httpx.Response(
                200, json={"candidates": [{"content": {"parts": [{"text": "caption"}]}}]}
            )
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
    # Must be a JSON number, not a string -- Veo rejects a string
    # durationSeconds with a 400 (confirmed against a real production
    # call, CIN-112).
    assert body["parameters"]["durationSeconds"] == 8
    assert isinstance(body["parameters"]["durationSeconds"], int)
    assert body["parameters"]["resolution"] == "1080p"


def test_download_follows_redirect_to_get_real_video_bytes() -> None:
    # CIN-113: a real production download hit a 302 from video_uri --
    # httpx doesn't follow redirects by default, so the un-followed
    # response's body (a small JSON error/redirect stub, not real
    # video bytes) got uploaded as if it were the video. Confirmed by
    # inspecting the actual corrupted file: 95 bytes of
    # {"error": {"code": 302, ...}} instead of an MP4.
    _REDIRECT_TARGET = "https://storage.googleapis.com/actual-video-bytes"
    real_video_bytes = b"real-mp4-bytes-not-a-redirect-stub"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"name": "operations/abc123"})
        if str(request.url) == _VIDEO_URI:
            return httpx.Response(302, headers={"location": _REDIRECT_TARGET})
        if str(request.url) == _REDIRECT_TARGET:
            return httpx.Response(200, content=real_video_bytes)
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

    with patch(
        "app.content_pipeline.video_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.mp4",
    ) as upload:
        veo_video_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler), sleep=_no_sleep
        )

    upload.assert_called_once_with(real_video_bytes, "video/mp4", "mp4")


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

    with pytest.raises(VideoGenerationFailedError):
        veo_video_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler), sleep=_no_sleep
        )


def test_start_400_does_not_leak_api_key_in_message() -> None:
    # CIN-111: predictLongRunning authenticates via a `?key=` query
    # param -- a bare httpx.HTTPStatusError's message includes the
    # full request URL (and therefore the key). Settings are patched
    # to a known fake key so this assertion is meaningful regardless
    # of what real key (if any) is configured in this environment.
    from types import SimpleNamespace
    from unittest.mock import patch

    fake_key = "fake-secret-key-should-not-leak"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})

    with (
        patch(
            "app.content_pipeline.video_generator.get_settings",
            return_value=SimpleNamespace(
                veo_model="fake-veo-model",
                veo_duration_seconds=8,
                veo_resolution="1080p",
                gemini_api_key=fake_key,
            ),
        ),
        pytest.raises(VideoGenerationFailedError) as exc_info,
    ):
        veo_video_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler), sleep=_no_sleep
        )
    message = str(exc_info.value)
    assert fake_key not in message
    assert "key=" not in message


def test_poll_5xx_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"name": "operations/abc123"})
        return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})

    with pytest.raises(TransientGenerationError):
        veo_video_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler), sleep=_no_sleep
        )


def test_poll_400_is_not_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"name": "operations/abc123"})
        return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})

    with pytest.raises(VideoGenerationFailedError):
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


def test_image_attachment_is_not_used_in_the_video_prompt() -> None:
    # Veo's own video-generation call has no documented way to take
    # arbitrary image/video/audio context (only text-to-video) -- an
    # image attachment must not change that request's prompt/body.
    # The caption sub-call (CIN-114) is a separate concern: it DOES use
    # image attachments as multimodal context, by design (same as a
    # plain text generation would) -- so it legitimately fetches the
    # attachment; this test only asserts the *video* request stays clean.
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "generateContent" in url:
            return httpx.Response(
                200, json={"candidates": [{"content": {"parts": [{"text": "caption"}]}}]}
            )
        if url.startswith("https://r2.example"):
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
        # The handler's own assertion (body["instances"][0]["prompt"])
        # is the real check here -- it fails loudly if the video
        # request ever picks up image content.
        veo_video_generator(payload, client=_client(handler), sleep=_no_sleep)


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


def test_successful_generation_includes_generated_caption() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "generateContent" in url:
            return httpx.Response(
                200, json={"candidates": [{"content": {"parts": [{"text": "Видео-подпись"}]}}]}
            )
        if request.method == "POST":
            return httpx.Response(200, json={"name": "operations/abc123"})
        if url == _VIDEO_URI:
            return httpx.Response(200, content=b"fake-video-bytes")
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

    with patch(
        "app.content_pipeline.video_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.mp4",
    ):
        result = veo_video_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler), sleep=_no_sleep
        )
    assert result["text"] == "Видео-подпись"


def test_caption_failure_does_not_fail_the_video_generation() -> None:
    # CIN-114: a caption is best-effort -- if it fails, the successful
    # (already paid-for) video must still be returned, just without "text".
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "generateContent" in url:
            return httpx.Response(500, json={"error": {"status": "INTERNAL"}})
        if request.method == "POST":
            return httpx.Response(200, json={"name": "operations/abc123"})
        if url == _VIDEO_URI:
            return httpx.Response(200, content=b"fake-video-bytes")
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

    with patch(
        "app.content_pipeline.video_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.mp4",
    ):
        result = veo_video_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler), sleep=_no_sleep
        )
    assert result["video_url"] == "https://media.cindra.example/abc.mp4"
    assert "text" not in result
