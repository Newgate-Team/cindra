import time
from collections.abc import Callable
from typing import Any

import httpx

from app.config import get_settings
from app.content_pipeline.attachments import build_attachment_context
from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.media_storage import upload_bytes
from app.content_pipeline.text_generator import generate_caption

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# 5xx and 429 are worth retrying (transient); anything else (400 bad
# key, 403 permission denied) won't fix itself on retry.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_POLL_INTERVAL_SECONDS = 10.0
_MAX_POLL_ATTEMPTS = 36  # ~6 minutes, generous for a Fast-tier video


class VideoGenerationFailedError(Exception):
    """Raised when Veo reports the operation as done but failed, it
    never finishes within the poll budget, or a call returns a
    non-retryable HTTP error. Not transient -- retrying would just pay
    for a whole new (paid) generation.

    Deliberately never raised via httpx.HTTPStatusError/
    raise_for_status() for the predictLongRunning/poll calls -- that
    exception's message includes the full request URL, and those calls
    authenticate via a `?key=` query param (CIN-111), so the raw API
    key would leak into job.error_message and straight into the UI
    otherwise.
    """


def _build_video_prompt(payload: dict[str, Any], attachment_texts: list[str] | None = None) -> str:
    lines = [f"Короткое видео на тему: {payload['topic']}."]
    brand_guide = payload.get("brand_guide")
    if brand_guide:
        lines.append(f"Стиль и бренд-гайд (соблюдать): {brand_guide}")
    for i, text in enumerate(attachment_texts or [], start=1):
        label = "Контекст из прикреплённого документа" if len(attachment_texts) == 1 else f"Контекст из документа {i}"
        lines.append(f"{label}: {text}")
    return "\n".join(lines)


def veo_video_generator(
    payload: dict[str, Any],
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Real Google Veo 3.1 Fast video-generation call.

    `payload` is a GenerationJob.input_payload built by the /content
    router: {"topic", "platform", "content_kind", "brand_guide"}.
    Without GEMINI_API_KEY configured this still reaches the real
    endpoint and fails with a real 400 API_KEY_INVALID -- proving the
    request is shaped correctly, not mocking the call away (verified
    manually against the real generativelanguage.googleapis.com
    endpoint; see CIN-55/CIN-57).

    Video generation is asynchronous: predictLongRunning kicks off an
    operation, then this polls it until done. `client`/`sleep` are
    only for tests to inject an httpx.MockTransport and a no-op sleep
    -- production always uses the default real client and time.sleep.

    The completed operation only gives a Google-hosted video URI that
    requires the API key to download and expires -- not safe to hand
    straight to a publisher, especially once a post can be scheduled
    for later (CIN-75) and that URI may have already expired by
    publish time. This downloads the bytes immediately and re-uploads
    them to R2 (CIN-56/CIN-78), returning a stable public `video_url`.

    Note: the exact shape parsed out of the completed operation
    (`response.generateVideoResponse.generatedSamples[0].video.uri`)
    is cross-checked against Google's documented request/response
    examples (see CIN-57), but still not verified against a real
    successful response -- a real production call with a valid, paid
    key reached predictLongRunning and got a 400 (request-validation
    error, not an auth error), so the request shape itself needs
    re-checking against the current Veo API spec (CIN-111) before this
    note can be considered resolved.
    """
    settings = get_settings()

    # Optional context files (CIN-97, up to 2 documents since CIN-107):
    # only documents' extracted text is used here -- Veo's
    # predictLongRunning has no documented way to accept an arbitrary
    # image/video/audio as generation context (only text-to-video), so
    # image/video/audio attachments aren't applied when content_type is
    # "video" rather than guessing at an unconfirmed request shape.
    attachment_texts = [
        build_attachment_context(attachment["url"], "document", client=client)["text"]
        for attachment in payload.get("attachments", [])
        if attachment["attachment_type"] == "document"
    ]

    prompt = _build_video_prompt(payload, attachment_texts=attachment_texts)
    post = client.post if client is not None else httpx.post
    get = client.get if client is not None else httpx.get
    params = {"key": settings.gemini_api_key}

    start_url = f"{_GEMINI_BASE_URL}/models/{settings.veo_model}:predictLongRunning"
    try:
        start_response = post(
            start_url,
            params=params,
            headers={"content-type": "application/json"},
            json={
                "instances": [{"prompt": prompt}],
                "parameters": {
                    "durationSeconds": settings.veo_duration_seconds,
                    "resolution": settings.veo_resolution,
                },
            },
            timeout=30.0,
        )
    except httpx.TransportError as exc:
        # Network-level failure (timeout, connection reset, DNS) --
        # distinct from an HTTP error response, and just as transient.
        raise TransientGenerationError(f"Veo API network error: {exc}") from exc
    if start_response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientGenerationError(
            f"Veo API {start_response.status_code}: {start_response.text[:500]}"
        )
    if start_response.status_code >= 400:
        raise VideoGenerationFailedError(
            f"Veo API {start_response.status_code}: {start_response.text[:500]}"
        )
    operation_name = start_response.json()["name"]

    operation_url = f"{_GEMINI_BASE_URL}/{operation_name}"
    for _ in range(_MAX_POLL_ATTEMPTS):
        sleep(_POLL_INTERVAL_SECONDS)
        try:
            poll_response = get(operation_url, params=params, timeout=30.0)
        except httpx.TransportError as exc:
            raise TransientGenerationError(f"Veo API network error: {exc}") from exc
        if poll_response.status_code in _RETRYABLE_STATUS_CODES:
            raise TransientGenerationError(
                f"Veo API {poll_response.status_code}: {poll_response.text[:500]}"
            )
        if poll_response.status_code >= 400:
            raise VideoGenerationFailedError(
                f"Veo API {poll_response.status_code}: {poll_response.text[:500]}"
            )
        operation = poll_response.json()
        if not operation.get("done"):
            continue

        if "error" in operation:
            raise VideoGenerationFailedError(f"Veo generation failed: {operation['error']}")

        samples = operation["response"]["generateVideoResponse"]["generatedSamples"]
        video_uri = samples[0]["video"]["uri"]
        try:
            # follow_redirects=True is required here (CIN-113): httpx
            # defaults to NOT following redirects, and this download
            # URL redirects (302) to the actual file. Without this,
            # download_response.content was a small JSON redirect/error
            # body instead of real video bytes -- status_code stayed
            # under 400, so it silently passed the check below and got
            # uploaded to R2 as if it were a valid video.
            download_response = get(
                video_uri,
                headers={"x-goog-api-key": settings.gemini_api_key},
                timeout=60.0,
                follow_redirects=True,
            )
        except httpx.TransportError as exc:
            raise TransientGenerationError(f"Veo API network error: {exc}") from exc
        if download_response.status_code >= 400:
            # video_uri is a Google-signed temporary download link, not
            # our own credential, but still not something to echo
            # whole into a user-facing error message (same defensive
            # posture as the key-bearing calls above, CIN-111).
            raise VideoGenerationFailedError(
                f"Veo video download {download_response.status_code}: "
                f"{download_response.text[:500]}"
            )
        video_url = upload_bytes(download_response.content, "video/mp4", "mp4")

        result: dict[str, Any] = {"video_url": video_url, "prompt": prompt}
        # CIN-114: a real caption, not just the raw topic, for the
        # "Подпись" field on the review-and-publish screen --
        # best-effort, never fails the (already successful, already
        # paid-for) video itself.
        caption = generate_caption(payload, client=client)
        if caption:
            result["text"] = caption
        return result

    raise VideoGenerationFailedError(
        f"Veo generation did not finish within {_MAX_POLL_ATTEMPTS} polls "
        f"({_MAX_POLL_ATTEMPTS * _POLL_INTERVAL_SECONDS:.0f}s)"
    )
