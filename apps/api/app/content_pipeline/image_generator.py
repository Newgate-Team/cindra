import base64
from typing import Any

import httpx

from app.config import get_settings
from app.content_pipeline.attachments import build_attachment_context
from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.media_storage import upload_bytes

_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"

# 5xx and 429 are worth retrying (transient); anything else (400 bad
# key, 403 permission denied) won't fix itself on retry.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _build_image_prompt(payload: dict[str, Any], attachment_text: str | None = None) -> str:
    lines = [f"Фотореалистичное изображение на тему: {payload['topic']}."]
    brand_guide = payload.get("brand_guide")
    if brand_guide:
        lines.append(f"Стиль и бренд-гайд (соблюдать): {brand_guide}")
    if attachment_text:
        lines.append(f"Контекст из прикреплённого документа: {attachment_text}")
    lines.append("Без текста, надписей и водяных знаков на изображении.")
    return "\n".join(lines)


def nano_banana_image_generator(
    payload: dict[str, Any], client: httpx.Client | None = None
) -> dict[str, Any]:
    """Real Google Gemini image-generation call via the Interactions API.

    `payload` is a GenerationJob.input_payload built by the /content
    router: {"topic", "platform", "content_kind", "brand_guide"}.
    Without GEMINI_API_KEY configured this still reaches the real
    endpoint and fails with a real 400/401 -- proving the request is
    shaped correctly, not mocking the call away. `client` is only for
    tests to inject an httpx.MockTransport -- production always uses
    the default real client.

    Replaces the old Imagen 4 `:predict` endpoint (deprecated by
    Google, shut down 2026-08-17 -- see CIN-58) with the Interactions
    API (`POST /v1beta/interactions`), auth via `x-goog-api-key`
    header rather than a `key` query param. Request/response shape
    cross-checked against Google's official Interactions API reference
    (ai.google.dev/api/interactions-api) across three independent
    fetches: the image-generation guide's curl example (endpoint,
    headers, `model`/`input` fields), the REST schema reference
    (`output_image.data`/`mime_type`), and explicit confirmation that
    a non-streaming request (the default -- no `stream` field sent)
    returns synchronously with `status: "completed"` and `output_image`
    already populated, so no polling loop is needed here (unlike Veo's
    predictLongRunning in video_generator.py).

    The Interactions API has no hosted URL for the generated image,
    only base64 -- this uploads the decoded bytes to R2 (CIN-56/CIN-78)
    and returns a real public `image_url`, directly usable as
    Post.image_url.
    """
    settings = get_settings()

    # Optional context file (CIN-97): a document's extracted text folds
    # into the prompt; an attached image becomes a reference image via
    # the Interactions API's array `input` form (image-to-image/edit,
    # ai.google.dev/gemini-api/docs/image-generation). Video/audio
    # attachments aren't usable here -- the Interactions API only
    # documents image reference input, not video/audio -- so they're
    # silently not applied when content_type is "image".
    attachment_text = None
    attachment_image_b64: str | None = None
    attachment_mime: str | None = None
    attachment_url = payload.get("attachment_url")
    if attachment_url:
        context = build_attachment_context(attachment_url, payload["attachment_type"], client=client)
        if context["kind"] == "text":
            attachment_text = context["text"]
        elif payload["attachment_type"] == "image":
            attachment_image_b64 = base64.b64encode(context["data"]).decode("ascii")
            attachment_mime = context["mime_type"]

    prompt = _build_image_prompt(payload, attachment_text=attachment_text)

    input_field: Any
    if attachment_image_b64:
        input_field = [
            {"type": "text", "text": prompt},
            {"type": "image", "mime_type": attachment_mime, "data": attachment_image_b64},
        ]
    else:
        input_field = prompt

    request_kwargs: dict[str, Any] = {
        "headers": {
            "x-goog-api-key": settings.gemini_api_key,
            "content-type": "application/json",
        },
        "json": {"model": settings.image_model, "input": input_field},
        "timeout": 60.0,
    }
    try:
        response = (
            client.post(_INTERACTIONS_URL, **request_kwargs)
            if client is not None
            else httpx.post(_INTERACTIONS_URL, **request_kwargs)
        )
    except httpx.TransportError as exc:
        # Network-level failure (timeout, connection reset, DNS) --
        # distinct from an HTTP error response, and just as transient.
        raise TransientGenerationError(f"Gemini Interactions API network error: {exc}") from exc

    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientGenerationError(
            f"Gemini Interactions API {response.status_code}: {response.text[:500]}"
        )
    response.raise_for_status()

    body = response.json()
    output_image = body["output_image"]
    image_bytes = base64.b64decode(output_image["data"])
    mime_type = output_image["mime_type"]
    extension = mime_type.split("/")[-1]
    image_url = upload_bytes(image_bytes, mime_type, extension)
    return {"image_url": image_url, "prompt": prompt}
