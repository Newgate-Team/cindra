import uuid
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy.orm import Session

from app.content_pipeline import heygen_generator
from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.video_dispatch import dispatch_video_generator
from app.content_pipeline.video_generator import VideoGenerationFailedError
from app.integrations import upsert_integration
from app.models import IntegrationProvider, User

_VIDEO_FILE = "https://files.heygen.com/video/v_1.mp4"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _no_sleep(_seconds: float) -> None:
    return None


def _payload(user: User, db: Session, **overrides) -> dict:
    integration = upsert_integration(
        db, user, IntegrationProvider.heygen, "users-own-key", "jane@example.com"
    )
    payload = {
        "provider": "heygen",
        "heygen_integration_id": str(integration.id),
        "heygen_request_id": "req-abc",
        "heygen_batch_id": "batch-1",
        "avatar_id": "lk_1",
        "voice_id": "voice-1",
        "script": "Привет, это тест",
        "aspect_ratio": "9:16",
        "title": None,
    }
    payload.update(overrides)
    return payload


class FakeHeyGen:
    """Scripted HeyGen + file host: `statuses` are what successive
    GET /v3/videos/{id} polls return."""

    def __init__(self, statuses: list[dict], video_url: str = _VIDEO_FILE) -> None:
        self.statuses = list(statuses)
        self.video_url = video_url
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "api.heygen.com" and request.method == "POST":
            return httpx.Response(200, json={"data": {"video_id": "v_1", "status": "waiting"}})
        if request.url.host == "api.heygen.com":
            status = self.statuses.pop(0)
            return httpx.Response(200, json={"data": {"id": "v_1", **status}})
        return httpx.Response(200, content=b"mp4-bytes")


def test_renders_downloads_and_stores_in_r2(user: User, db: Session) -> None:
    fake = FakeHeyGen([{"status": "processing"}, {"status": "completed", "video_url": _VIDEO_FILE}])
    with (
        _client(fake) as client,
        patch(
            "app.content_pipeline.heygen_generator.upload_bytes",
            return_value="https://media.cindra.test/abc.mp4",
        ) as upload,
    ):
        output = heygen_generator.heygen_video_generator(_payload(user, db), client, _no_sleep)

    assert output == {
        "video_url": "https://media.cindra.test/abc.mp4",
        "script": "Привет, это тест",
        "heygen_video_id": "v_1",
    }
    upload.assert_called_once_with(b"mp4-bytes", "video/mp4", "mp4")
    api_calls = [r for r in fake.requests if r.url.host == "api.heygen.com"]
    assert all(r.headers["x-api-key"] == "users-own-key" for r in api_calls)
    download = next(r for r in fake.requests if r.url.host == "files.heygen.com")
    # The file host must never receive the user's key.
    assert "x-api-key" not in download.headers


def test_retry_after_a_transient_poll_failure_reuses_the_idempotency_key(
    user: User, db: Session
) -> None:
    """A Celery retry re-runs the whole generator, create call included
    -- the same Idempotency-Key is what stops HeyGen from starting (and
    billing the user for) a second render."""
    payload = _payload(user, db)
    create_keys: list[str] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            create_keys.append(request.headers["idempotency-key"])
            return httpx.Response(200, json={"data": {"video_id": "v_1"}})
        return httpx.Response(503, json={"error": {"message": "busy"}})

    with _client(flaky) as client, pytest.raises(TransientGenerationError):
        heygen_generator.heygen_video_generator(payload, client, _no_sleep)

    fake = FakeHeyGen([{"status": "completed", "video_url": _VIDEO_FILE}])
    with (
        _client(fake) as client,
        patch("app.content_pipeline.heygen_generator.upload_bytes", return_value="https://r2/x.mp4"),
    ):
        heygen_generator.heygen_video_generator(payload, client, _no_sleep)
    create_keys.append(
        next(r for r in fake.requests if r.method == "POST").headers["idempotency-key"]
    )

    assert create_keys == ["req-abc", "req-abc"]


def test_failed_render_reports_heygens_reason(user: User, db: Session) -> None:
    fake = FakeHeyGen([{"status": "failed", "failure_message": "Avatar not usable"}])
    with (
        _client(fake) as client,
        pytest.raises(VideoGenerationFailedError, match="Avatar not usable"),
    ):
        heygen_generator.heygen_video_generator(_payload(user, db), client, _no_sleep)


def test_empty_wallet_gets_an_actionable_message(user: User, db: Session) -> None:
    def broke(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": {"code": "insufficient_credit"}})

    with (
        _client(broke) as client,
        pytest.raises(VideoGenerationFailedError, match="пополните кошелёк"),
    ):
        heygen_generator.heygen_video_generator(_payload(user, db), client, _no_sleep)


def test_revoked_key_tells_the_user_to_reconnect(user: User, db: Session) -> None:
    def revoked(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "unauthorized"}})

    with (
        _client(revoked) as client,
        pytest.raises(VideoGenerationFailedError, match="подключите HeyGen заново"),
    ):
        heygen_generator.heygen_video_generator(_payload(user, db), client, _no_sleep)


def test_video_on_an_unexpected_host_is_not_downloaded(user: User, db: Session) -> None:
    fake = FakeHeyGen(
        [{"status": "completed", "video_url": "https://evil.example.com/v.mp4"}],
        video_url="https://evil.example.com/v.mp4",
    )
    with (
        _client(fake) as client,
        pytest.raises(VideoGenerationFailedError, match="evil.example.com"),
    ):
        heygen_generator.heygen_video_generator(_payload(user, db), client, _no_sleep)
    assert all(r.url.host == "api.heygen.com" for r in fake.requests)


@pytest.mark.parametrize(
    "url",
    [
        "https://files.heygen.com/video/v.mp4",
        "https://resource.heygen.ai/video/v.mp4",
    ],
)
def test_heygen_file_hosts_are_allowed(url: str) -> None:
    assert heygen_generator._is_allowed_result_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://files.heygen.com/video/v.mp4",
        "https://heygen.com.evil.example/v.mp4",
        "https://notheygen.com/v.mp4",
    ],
)
def test_lookalike_hosts_are_rejected(url: str) -> None:
    assert not heygen_generator._is_allowed_result_url(url)


def test_disconnected_integration_fails_without_calling_heygen(user: User, db: Session) -> None:
    payload = _payload(user, db, heygen_integration_id=str(uuid.uuid4()))

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call HeyGen without a key")

    with (
        _client(unreachable) as client,
        pytest.raises(VideoGenerationFailedError, match="отключён"),
    ):
        heygen_generator.heygen_video_generator(payload, client, _no_sleep)


def test_storage_failure_points_the_user_at_the_paid_render(user: User, db: Session) -> None:
    fake = FakeHeyGen(
        [
            {
                "status": "completed",
                "video_url": _VIDEO_FILE,
                "video_page_url": "https://app.heygen.com/videos/v_1",
            }
        ]
    )
    with (
        _client(fake) as client,
        patch(
            "app.content_pipeline.heygen_generator.upload_bytes",
            side_effect=ValueError("R2 not configured"),
        ),
        pytest.raises(VideoGenerationFailedError, match="app.heygen.com/videos/v_1"),
    ):
        heygen_generator.heygen_video_generator(_payload(user, db), client, _no_sleep)


def test_render_that_never_finishes_times_out(user: User, db: Session) -> None:
    fake = FakeHeyGen([{"status": "processing"}] * 3)
    with (
        _client(fake) as client,
        patch.object(heygen_generator, "_MAX_POLL_ATTEMPTS", 3),
        pytest.raises(VideoGenerationFailedError, match="не закончил рендер"),
    ):
        heygen_generator.heygen_video_generator(_payload(user, db), client, _no_sleep)


def test_dispatcher_routes_heygen_jobs_to_the_heygen_generator() -> None:
    with patch(
        "app.content_pipeline.video_dispatch.heygen_video_generator",
        return_value={"video_url": "x"},
    ) as generator:
        assert dispatch_video_generator({"provider": "heygen"}) == {"video_url": "x"}
    generator.assert_called_once()
