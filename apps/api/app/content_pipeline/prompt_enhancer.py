import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# CIN-142: much tighter than text generation's 2048 -- the deliverable
# is a single <=150-word image prompt, and output tokens cost 8.3x
# input on this model (CIN-98).
_MAX_OUTPUT_TOKENS = 512

# The rules restate CIN-117 (never a blanket "no text on the image"),
# CIN-125 (on-image text: short and spelled correctly -- image models
# reliably garble longer phrases) and CIN-132 (natural moment, negative
# space, artifact exclusions) -- when the enhancer is in play, THIS is
# where those production lessons live, because the image model no
# longer sees _build_image_prompt's Russian wrapper at all.
_META_PROMPT = (
    "You are a prompt engineer for a text-to-image model. Rewrite the request "
    "below into ONE detailed English image-generation prompt.\n"
    "Cover in a single paragraph: main subject and what it is doing; setting; "
    "composition and framing; lighting and mood; photographic or artistic "
    "style; color palette.\n"
    "Rules:\n"
    "- Photorealistic by default; use a different style only if the request or "
    "brand guide clearly asks for one.\n"
    "- A natural, believable moment rather than a staged stock pose; keep some "
    "clear negative space near one edge for a caption overlay.\n"
    "- If the request implies text on the image (a sign, slogan, screen, "
    "banner), include that text verbatim in its original language as a short "
    "phrase of at most 6 words, spelled correctly. Do not add any other text "
    "to the image.\n"
    "- No distorted faces, extra fingers, fake user interfaces, or unreadable "
    "background writing.\n"
    "- Do not invent brand names, numbers or claims that are not in the "
    "request or brand guide.\n"
    "- Return ONLY the prompt text: no explanations, no quotes, at most 150 "
    "words."
)


def _build_enhancer_input(
    payload: dict[str, Any], attachment_texts: list[str] | None = None
) -> str:
    lines = [_META_PROMPT, f"Request (may be in Russian): {payload['topic']}"]
    brand_guide = payload.get("brand_guide")
    if brand_guide:
        lines.append(f"Brand guide to respect: {brand_guide}")
    for text in attachment_texts or []:
        lines.append(f"Context from an attached document: {text}")
    return "\n".join(lines)


def enhance_image_prompt(
    payload: dict[str, Any],
    attachment_texts: list[str] | None = None,
    client: httpx.Client | None = None,
) -> str | None:
    """Best-effort rewrite of the user's raw topic into a detailed
    English image prompt (CIN-142).

    The raw topic alone ("пост про запуск кофейного бренда") gives the
    image model almost nothing to work with -- and it follows English
    prompts noticeably better than Russian ones. One cheap Flash-Lite
    call closes both gaps.

    Swallows every failure and returns None -- the caller falls back to
    _build_image_prompt's wrapper, so a hiccup here can never fail (or
    delay into a retry) an image generation the user is paying for.
    Same contract as generate_caption (CIN-114); like the caption call,
    this internal request is not billed against the user's text limit.
    """
    settings = get_settings()
    url = f"{_GEMINI_BASE_URL}/{settings.gemini_model}:generateContent"
    request_kwargs: dict[str, Any] = {
        "params": {"key": settings.gemini_api_key},
        "headers": {"content-type": "application/json"},
        "json": {
            "contents": [
                {"parts": [{"text": _build_enhancer_input(payload, attachment_texts)}]}
            ],
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
        if response.status_code != 200:
            logger.warning(
                "Image prompt enhancer got HTTP %s, falling back to baseline prompt",
                response.status_code,
            )
            return None
        body = response.json()
        text = "".join(
            part["text"]
            for part in body["candidates"][0]["content"]["parts"]
            if "text" in part
        ).strip()
        return text or None
    except Exception:
        logger.warning(
            "Image prompt enhancer failed, falling back to baseline prompt",
            exc_info=True,
        )
        return None
