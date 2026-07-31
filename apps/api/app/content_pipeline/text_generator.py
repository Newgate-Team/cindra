from typing import Any

import httpx

from app.config import get_settings
from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.prompts import build_text_prompt
from app.models import SocialPlatform

_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"

# 5xx and 429 are worth retrying (transient); anything else (401/403
# bad key, 400 bad request) won't fix itself on retry.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def anthropic_text_generator(
    payload: dict[str, Any], client: httpx.Client | None = None
) -> dict[str, Any]:
    """Real Anthropic Messages API call.

    `payload` is a GenerationJob.input_payload built by the /content
    router: {"topic", "platform", "content_kind", "brand_guide"}.
    Without ANTHROPIC_API_KEY configured (see gate ticket CIN-49) this
    still reaches the real endpoint and fails with a real 401 --
    proving the request is shaped correctly, not mocking the call away
    (verified manually; see CIN-49). `client` is only for tests to
    inject an httpx.MockTransport -- production always uses the
    default real client.
    """
    settings = get_settings()
    prompt = build_text_prompt(
        topic=payload["topic"],
        platform=SocialPlatform(payload["platform"]),
        content_kind=payload.get("content_kind", "post"),
        brand_guide=payload.get("brand_guide"),
    )

    request_kwargs: dict[str, Any] = {
        "headers": {
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        "json": {
            "model": settings.anthropic_model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        },
        "timeout": 30.0,
    }
    response = (
        client.post(_ANTHROPIC_MESSAGES_URL, **request_kwargs)
        if client is not None
        else httpx.post(_ANTHROPIC_MESSAGES_URL, **request_kwargs)
    )

    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientGenerationError(
            f"Anthropic API {response.status_code}: {response.text[:500]}"
        )
    response.raise_for_status()

    body = response.json()
    text = "".join(
        block["text"] for block in body["content"] if block.get("type") == "text"
    )
    return {"text": text, "prompt": prompt}
