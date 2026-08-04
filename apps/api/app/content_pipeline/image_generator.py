import base64
from typing import Any

import httpx

from app.config import get_settings
from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.media_storage import upload_bytes

_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"

# 5xx and 429 are worth retrying (transient); anything else (400 bad
# key, 403 permission denied) won't fix itself on retry.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _build_image_prompt(payload: dict[str, Any]) -> str:
    lines = [f"Фотореалистичное изображение на тему: {payload['topic']}."]
    brand_guide = payload.get("brand_guide")
    if brand_guide:
        lines.append(f"Стиль и бренд-гайд (соблюдать): {brand_guide}")
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
    prompt = _build_image_prompt(payload)

    request_kwargs: dict[str, Any] = {
        "headers": {
            "x-goog-api-key": settings.gemini_api_key,
            "content-type": "application/json",
        },
        "json": {"model": settings.image_model, "input": prompt},
        "timeout": 60.0,
    }
    response = (
        client.post(_INTERACTIONS_URL, **request_kwargs)
        if client is not None
        else httpx.post(_INTERACTIONS_URL, **request_kwargs)
    )

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
