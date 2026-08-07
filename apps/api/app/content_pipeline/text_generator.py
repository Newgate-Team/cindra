import base64
from typing import Any

import httpx

from app.config import get_settings
from app.content_pipeline.attachments import build_attachment_context
from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.prompts import build_text_prompt
from app.models import SocialPlatform

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# 5xx and 429 are worth retrying (transient); anything else (400 bad
# key, 403 permission denied) won't fix itself on retry.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Output tokens cost 8.3x input on this model ($2.50 vs $0.30/1M) --
# an explicit cap is a cheap backstop against paying for a rambling
# response, with generous headroom above anything this app actually
# asks for: the longest legitimate content_kind (video_script, shot-by
# -shot with timecodes) still lands well under this for any supported
# platform (CIN-98).
_MAX_OUTPUT_TOKENS = 2048


def gemini_text_generator(
    payload: dict[str, Any], client: httpx.Client | None = None
) -> dict[str, Any]:
    """Real Google Gemini generateContent API call.

    `payload` is a GenerationJob.input_payload built by the /content
    router: {"topic", "platform", "content_kind", "brand_guide"}.
    Without GEMINI_API_KEY configured (see gate ticket CIN-53) this
    still reaches the real endpoint and fails with a real 400
    API_KEY_INVALID -- proving the request is shaped correctly, not
    mocking the call away (verified manually against the real
    generativelanguage.googleapis.com endpoint; see CIN-53). `client`
    is only for tests to inject an httpx.MockTransport -- production
    always uses the default real client.
    """
    settings = get_settings()

    # Optional context files (CIN-97, up to 5 mixed since CIN-107): each
    # document's extracted text folds into the prompt string; each
    # image/video/audio attachment instead becomes an extra multimodal
    # `parts` entry below -- Gemini reads it directly rather than us
    # describing it in words.
    attachment_texts: list[str] = []
    attachment_parts: list[dict[str, Any]] = []
    for attachment in payload.get("attachments", []):
        context = build_attachment_context(
            attachment["url"], attachment["attachment_type"], client=client
        )
        if context["kind"] == "text":
            attachment_texts.append(context["text"])
        else:
            attachment_parts.append(
                {
                    "inline_data": {
                        "mime_type": context["mime_type"],
                        "data": base64.b64encode(context["data"]).decode("ascii"),
                    }
                }
            )

    prompt = build_text_prompt(
        topic=payload["topic"],
        platform=SocialPlatform(payload["platform"]),
        content_kind=payload.get("content_kind", "post"),
        brand_guide=payload.get("brand_guide"),
        attachment_texts=attachment_texts,
    )

    parts: list[dict[str, Any]] = [{"text": prompt}]
    parts.extend(attachment_parts)

    url = f"{_GEMINI_BASE_URL}/{settings.gemini_model}:generateContent"
    request_kwargs: dict[str, Any] = {
        "params": {"key": settings.gemini_api_key},
        "headers": {"content-type": "application/json"},
        "json": {
            "contents": [{"parts": parts}],
            "generationConfig": {"maxOutputTokens": _MAX_OUTPUT_TOKENS},
        },
        "timeout": 30.0,
    }
    try:
        response = (
            client.post(url, **request_kwargs)
            if client is not None
            else httpx.post(url, **request_kwargs)
        )
    except httpx.TransportError as exc:
        # Network-level failure (timeout, connection reset, DNS) --
        # distinct from an HTTP error response, and just as transient.
        raise TransientGenerationError(f"Gemini API network error: {exc}") from exc

    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientGenerationError(
            f"Gemini API {response.status_code}: {response.text[:500]}"
        )
    response.raise_for_status()

    body = response.json()
    text = "".join(
        part["text"]
        for part in body["candidates"][0]["content"]["parts"]
        if "text" in part
    )
    return {"text": text, "prompt": prompt}
