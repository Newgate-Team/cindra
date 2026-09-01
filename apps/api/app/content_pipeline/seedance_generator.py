import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import get_settings
from app.content_pipeline.aspect_ratio import video_aspect_ratio
from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.media_storage import upload_bytes
from app.content_pipeline.video_generator import (
    VideoGenerationFailedError,
    _build_video_prompt,
)

_QUEUE_BASE_URL = "https://queue.fal.run"

# 5xx and 429 are worth retrying (transient); anything else (401 bad
# key, 422 bad input) won't fix itself on retry.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_POLL_INTERVAL_SECONDS = 10.0
_MAX_POLL_ATTEMPTS = 90  # ~15 minutes: a 30s clip takes far longer than Veo's 8s

# The finished video's URL arrives in fal's response body -- fetching
# an attacker-controllable URL is the same SSRF shape
# _validate_media_url closes for TikTok (CIN-134), so only fal's own
# hosts are accepted, and a redirect away from them is treated as a
# failure rather than followed.
_ALLOWED_RESULT_HOSTS = ("fal.media", "fal.run")


def _is_allowed_result_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return False
    return any(
        parts.hostname == host or parts.hostname.endswith("." + host)
        for host in _ALLOWED_RESULT_HOSTS
    )


def seedance_video_generator(
    payload: dict[str, Any],
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Seedance 2.5 text-to-video via fal.ai's queue API (CIN-144).

    Why a second provider: Veo 3.1 generates 8-second clips, so the
    studio's "Полное авто" was cramming a whole script into one clip
    (the root cause of the mushy results). Seedance 2.5 natively
    generates up to 30 seconds with synchronized audio -- an entire
    short in one call, and its multi-shot architecture actually uses
    the script's shot structure. Reuses Veo's prompt builder on
    purpose: same creative-direction contract, different renderer.

    Queue protocol (submit -> poll status -> fetch result), input
    schema (duration/aspect_ratio as strings, "auto" allowed) and the
    output shape (video.url) verified against fal.ai's official model
    page and queue docs on 2026-09-01. Polling/result URLs are built
    locally from the request_id rather than taken from the response's
    status_url/response_url -- one less attacker-controllable URL to
    follow. Auth via the Authorization header, so raising with the
    response text can't leak the key (unlike Veo's ?key= param,
    CIN-111).

    No caption call: this generator only serves the video studio,
    whose clips are published through the project flow with the user's
    own caption -- the CIN-114 caption is for the /content pipeline.
    """
    settings = get_settings()
    if not settings.fal_key:
        # Permanent, not transient: retrying won't conjure a key. The
        # router only routes here when the key is set, so hitting this
        # means the key was removed between enqueue and execution.
        raise VideoGenerationFailedError(
            "FAL_KEY не настроен — генерация через Seedance недоступна"
        )

    prompt = _build_video_prompt(payload)
    post = client.post if client is not None else httpx.post
    get = client.get if client is not None else httpx.get
    headers = {"authorization": f"Key {settings.fal_key}"}

    submit_url = f"{_QUEUE_BASE_URL}/{settings.seedance_model}"
    try:
        submit_response = post(
            submit_url,
            headers={**headers, "content-type": "application/json"},
            json={
                "prompt": prompt,
                "duration": settings.seedance_duration,
                "resolution": settings.seedance_resolution,
                "aspect_ratio": video_aspect_ratio(payload) or "auto",
                "generate_audio": True,
            },
            timeout=30.0,
        )
    except httpx.TransportError as exc:
        raise TransientGenerationError(f"fal.ai queue network error: {exc}") from exc
    if submit_response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientGenerationError(
            f"fal.ai queue {submit_response.status_code}: {submit_response.text[:500]}"
        )
    if submit_response.status_code >= 400:
        raise VideoGenerationFailedError(
            f"fal.ai queue {submit_response.status_code}: {submit_response.text[:500]}"
        )
    request_id = submit_response.json().get("request_id")
    if not request_id:
        raise VideoGenerationFailedError("fal.ai queue не вернул request_id")

    request_url = f"{_QUEUE_BASE_URL}/{settings.seedance_model}/requests/{request_id}"
    for _ in range(_MAX_POLL_ATTEMPTS):
        sleep(_POLL_INTERVAL_SECONDS)
        try:
            status_response = get(f"{request_url}/status", headers=headers, timeout=30.0)
        except httpx.TransportError as exc:
            raise TransientGenerationError(f"fal.ai queue network error: {exc}") from exc
        if status_response.status_code in _RETRYABLE_STATUS_CODES:
            raise TransientGenerationError(
                f"fal.ai queue {status_response.status_code}: {status_response.text[:500]}"
            )
        if status_response.status_code >= 400:
            raise VideoGenerationFailedError(
                f"fal.ai queue {status_response.status_code}: {status_response.text[:500]}"
            )
        if status_response.json().get("status") != "COMPLETED":
            # IN_QUEUE / IN_PROGRESS -- keep waiting.
            continue

        try:
            result_response = get(request_url, headers=headers, timeout=30.0)
        except httpx.TransportError as exc:
            raise TransientGenerationError(f"fal.ai queue network error: {exc}") from exc
        if result_response.status_code >= 400:
            raise TransientGenerationError(
                f"fal.ai result {result_response.status_code}: {result_response.text[:500]}"
            )
        result = result_response.json()
        if result.get("error"):
            raise VideoGenerationFailedError(
                f"Seedance generation failed: {result['error']} "
                f"({result.get('error_type', 'unknown')})"
            )
        video_url = (result.get("video") or {}).get("url")
        if not video_url:
            raise VideoGenerationFailedError("fal.ai вернул ответ без video.url")
        if not _is_allowed_result_url(video_url):
            raise VideoGenerationFailedError(
                "fal.ai вернул видео с неожиданного хоста — скачивание отклонено"
            )

        # No auth header here: the media host must never see the fal
        # key, and public fal.media files don't need it.
        try:
            download_response = get(video_url, timeout=120.0)
        except httpx.TransportError as exc:
            raise TransientGenerationError(f"fal.ai video download network error: {exc}") from exc
        if download_response.status_code != 200:
            # A redirect (3xx) would escape the host allowlist above,
            # so it fails here too rather than being followed -- unlike
            # Veo's download (CIN-113), fal.media serves files directly.
            raise TransientGenerationError(
                f"fal.ai video download {download_response.status_code}: "
                f"{download_response.text[:200]}"
            )
        video_url_r2 = upload_bytes(download_response.content, "video/mp4", "mp4")
        return {"video_url": video_url_r2, "prompt": prompt}

    raise VideoGenerationFailedError(
        f"Seedance generation did not finish within {_MAX_POLL_ATTEMPTS} polls "
        f"({_MAX_POLL_ATTEMPTS * _POLL_INTERVAL_SECONDS:.0f}s)"
    )
