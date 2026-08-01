from typing import Any

import httpx

from app.config import get_settings
from app.content_pipeline.errors import TransientGenerationError

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

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


def imagen_image_generator(
    payload: dict[str, Any], client: httpx.Client | None = None
) -> dict[str, Any]:
    """Real Google Imagen 4 image-generation call (predict endpoint).

    `payload` is a GenerationJob.input_payload built by the /content
    router: {"topic", "platform", "content_kind", "brand_guide"}.
    Without GEMINI_API_KEY configured this still reaches the real
    endpoint and fails with a real 400 API_KEY_INVALID -- proving the
    request is shaped correctly, not mocking the call away (verified
    manually against the real generativelanguage.googleapis.com
    endpoint; see CIN-54). `client` is only for tests to inject an
    httpx.MockTransport -- production always uses the default real
    client.

    Returns the image as base64 (`image_base64`), not a URL -- Imagen's
    predict response has no hosted URL. Turning this into a public
    Post.image_url is a separate concern; see gate ticket CIN-56.
    """
    settings = get_settings()
    prompt = _build_image_prompt(payload)

    url = f"{_GEMINI_BASE_URL}/{settings.imagen_model}:predict"
    request_kwargs: dict[str, Any] = {
        "params": {"key": settings.gemini_api_key},
        "headers": {"content-type": "application/json"},
        "json": {
            "instances": [{"prompt": prompt}],
            "parameters": {"sampleCount": 1},
        },
        "timeout": 60.0,
    }
    response = (
        client.post(url, **request_kwargs)
        if client is not None
        else httpx.post(url, **request_kwargs)
    )

    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientGenerationError(
            f"Imagen API {response.status_code}: {response.text[:500]}"
        )
    response.raise_for_status()

    body = response.json()
    image_base64 = body["predictions"][0]["bytesBase64Encoded"]
    return {"image_base64": image_base64, "prompt": prompt}
