import json
from unittest.mock import patch

import httpx
import pytest

from app.config import get_settings
from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.seedance_generator import seedance_video_generator
from app.content_pipeline.video_generator import VideoGenerationFailedError

_SUBMIT_URL = "https://queue.fal.run/bytedance/seedance-2.5/text-to-video"
_REQUEST_URL = f"{_SUBMIT_URL}/requests/req-123"
_RESULT_VIDEO_URL = "https://v3b.fal.media/files/generated.mp4"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _no_sleep(_: float) -> None:
    pass


@pytest.fixture(autouse=True)
def _configured_fal_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "fal_key", "test-fal-key")


def _happy_handler(captured: dict, poll_count: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        captured.setdefault("requests", []).append((request.method, url))
        if request.method == "POST":
            captured["submit_headers"] = dict(request.headers)
            captured["submit_body"] = json.loads(request.content)
            return httpx.Response(200, json={"request_id": "req-123"})
        if url == f"{_REQUEST_URL}/status":
            poll_count["n"] += 1
            if poll_count["n"] < 2:
                return httpx.Response(200, json={"status": "IN_PROGRESS"})
            return httpx.Response(200, json={"status": "COMPLETED"})
        if url == _REQUEST_URL:
            captured["result_headers"] = dict(request.headers)
            return httpx.Response(
                200, json={"video": {"url": _RESULT_VIDEO_URL}, "seed": 42}
            )
        if url == _RESULT_VIDEO_URL:
            captured["download_headers"] = dict(request.headers)
            return httpx.Response(200, content=b"fake-seedance-bytes")
        raise AssertionError(f"unexpected request: {request.method} {url}")

    return handler


def test_submits_polls_downloads_and_uploads_to_r2() -> None:
    captured: dict = {}
    with patch(
        "app.content_pipeline.seedance_generator.upload_bytes",
        return_value="https://media.cindra.example/clip.mp4",
    ) as upload:
        result = seedance_video_generator(
            {"topic": "утренний кофе", "aspect_ratio": "9:16"},
            client=_client(_happy_handler(captured, {"n": 0})),
            sleep=_no_sleep,
        )

    upload.assert_called_once_with(b"fake-seedance-bytes", "video/mp4", "mp4")
    assert result["video_url"] == "https://media.cindra.example/clip.mp4"
    assert "утренний кофе" in result["prompt"]
    assert captured["requests"][0] == ("POST", _SUBMIT_URL)
    assert captured["submit_headers"]["authorization"] == "Key test-fal-key"
    body = captured["submit_body"]
    assert "утренний кофе" in body["prompt"]
    # Strings by fal's schema -- unlike Veo's numeric durationSeconds
    # (CIN-112). 15s, not the model's 30s max: fal bills per second, and
    # CIN-146's long-clip plan limits are priced off this number.
    assert body["duration"] == "15"
    assert body["resolution"] == "720p"
    assert body["aspect_ratio"] == "9:16"
    assert body["generate_audio"] is True
    # The media host must never see the fal key
    assert "authorization" not in captured["download_headers"]


def test_no_aspect_ratio_sends_auto() -> None:
    captured: dict = {}
    with patch(
        "app.content_pipeline.seedance_generator.upload_bytes",
        return_value="https://media.cindra.example/clip.mp4",
    ):
        seedance_video_generator(
            {"topic": "x"},
            client=_client(_happy_handler(captured, {"n": 0})),
            sleep=_no_sleep,
        )
    assert captured["submit_body"]["aspect_ratio"] == "auto"


def test_missing_fal_key_is_permanent_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "fal_key", "")
    with pytest.raises(VideoGenerationFailedError):
        seedance_video_generator({"topic": "x"}, client=_client(lambda r: None), sleep=_no_sleep)


def test_submit_429_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "rate limited"})

    with pytest.raises(TransientGenerationError):
        seedance_video_generator({"topic": "x"}, client=_client(handler), sleep=_no_sleep)


def test_submit_422_is_permanent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "bad input"})

    with pytest.raises(VideoGenerationFailedError):
        seedance_video_generator({"topic": "x"}, client=_client(handler), sleep=_no_sleep)


def test_result_error_field_is_permanent_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST":
            return httpx.Response(200, json={"request_id": "req-123"})
        if url.endswith("/status"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        return httpx.Response(
            200, json={"error": "generation rejected", "error_type": "content_policy"}
        )

    with pytest.raises(VideoGenerationFailedError) as exc_info:
        seedance_video_generator({"topic": "x"}, client=_client(handler), sleep=_no_sleep)
    assert "generation rejected" in str(exc_info.value)


def test_off_host_video_url_is_rejected_without_download() -> None:
    # Same SSRF discipline as TikTok's _validate_media_url (CIN-134):
    # the URL comes from a response body, so an unexpected host must
    # not be fetched at all.
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        fetched.append(url)
        if request.method == "POST":
            return httpx.Response(200, json={"request_id": "req-123"})
        if url.endswith("/status"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        if url == _REQUEST_URL:
            return httpx.Response(
                200, json={"video": {"url": "https://evil.example.com/clip.mp4"}}
            )
        raise AssertionError(f"must not fetch {url}")

    with pytest.raises(VideoGenerationFailedError):
        seedance_video_generator({"topic": "x"}, client=_client(handler), sleep=_no_sleep)
    assert "https://evil.example.com/clip.mp4" not in fetched


def test_download_redirect_is_not_followed() -> None:
    # A 3xx on the media URL could bounce outside the allowlisted
    # hosts, so it fails rather than being followed (fal.media serves
    # files directly, unlike Veo's redirecting URIs -- CIN-113).
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST":
            return httpx.Response(200, json={"request_id": "req-123"})
        if url.endswith("/status"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        if url == _REQUEST_URL:
            return httpx.Response(200, json={"video": {"url": _RESULT_VIDEO_URL}})
        return httpx.Response(302, headers={"location": "https://evil.example.com/x"})

    with pytest.raises(TransientGenerationError):
        seedance_video_generator({"topic": "x"}, client=_client(handler), sleep=_no_sleep)


def test_never_completing_generation_fails_after_poll_budget() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"request_id": "req-123"})
        return httpx.Response(200, json={"status": "IN_QUEUE"})

    with pytest.raises(VideoGenerationFailedError) as exc_info:
        seedance_video_generator({"topic": "x"}, client=_client(handler), sleep=_no_sleep)
    assert "did not finish" in str(exc_info.value)
