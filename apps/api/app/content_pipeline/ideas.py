"""Content ideas for the dashboard: "what should I post this week?".

One synchronous Gemini call that returns a handful of concrete ideas
the user can turn into a post, a picture or a video with one click.
Tuned to be specific (a hook, an angle) and to avoid repeating what the
user already made recently.
"""

import json
from typing import Any

import httpx

from app.config import get_settings
from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.video_studio import VideoStudioFailedError

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

IDEA_FORMATS = ("post", "image", "video")
IDEA_COUNT = 6

_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "title": {"type": "STRING"},
            "hook": {"type": "STRING"},
            "format": {"type": "STRING", "enum": list(IDEA_FORMATS)},
        },
        "required": ["title", "hook", "format"],
    },
}


def build_ideas_prompt(
    brand_guide: str | None, recent_topics: list[str], focus: str | None
) -> str:
    lines = [
        f"Придумай {IDEA_COUNT} разных идей контента для соцсетей на ближайшую неделю.",
        (
            "Каждая идея — конкретная тема, которую можно сразу отдать в работу, а не общее "
            "направление: «Как мы выбираем зерно для осеннего кофе», а не «Расскажи о продукте»."
        ),
        (
            "Для каждой: title — тема в одну строку (до 90 символов); hook — первая фраза поста "
            "или ролика, которая заставит остановиться (до 140 символов); format — post "
            "(текстовый пост), image (картинка с подписью) или video (короткий вертикальный "
            "ролик). Смешивай форматы."
        ),
        "Пиши по-русски.",
    ]
    if brand_guide:
        lines.append(f"Бренд-гайд (соблюдать тон и стиль): {brand_guide}")
    if focus:
        lines.append(f"Фокус недели: {focus}")
    if recent_topics:
        recent = "; ".join(t[:80] for t in recent_topics[:10])
        lines.append(f"Недавно уже делали (не повторяй): {recent}")
    return "\n".join(lines)


def parse_ideas(raw: str) -> list[dict[str, str]]:
    """Validate the model's JSON; drop anything malformed rather than failing the lot."""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise VideoStudioFailedError("Модель вернула не JSON") from exc
    if not isinstance(data, list):
        raise VideoStudioFailedError("Модель вернула неожиданный формат")
    ideas: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        hook = str(item.get("hook", "")).strip()
        fmt = str(item.get("format", "post")).strip()
        if not title:
            continue
        ideas.append(
            {
                "title": title[:120],
                "hook": hook[:200],
                "format": fmt if fmt in IDEA_FORMATS else "post",
            }
        )
    if not ideas:
        raise VideoStudioFailedError("Модель не предложила ни одной идеи")
    return ideas[:IDEA_COUNT]


def generate_ideas(
    brand_guide: str | None,
    recent_topics: list[str],
    focus: str | None = None,
    client: httpx.Client | None = None,
) -> list[dict[str, str]]:
    settings = get_settings()
    # gemini_api_base_url: points at devtools/gemini_stub.py for a local run
    # without a key; the default is Google's own endpoint.
    url = (
        f"{settings.gemini_api_base_url.rstrip('/')}/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    request_kwargs: dict[str, Any] = {
        "params": {"key": settings.gemini_api_key},
        "headers": {"content-type": "application/json"},
        "json": {
            "contents": [
                {"parts": [{"text": build_ideas_prompt(brand_guide, recent_topics, focus)}]}
            ],
            "generationConfig": {
                "maxOutputTokens": 2048,
                "temperature": 1.0,
                "responseMimeType": "application/json",
                "responseSchema": _SCHEMA,
            },
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
        raise TransientGenerationError(f"Gemini API network error: {exc}") from exc
    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientGenerationError(f"Gemini API {response.status_code}")
    if response.status_code >= 400:
        # Not raise_for_status(): its message embeds the URL with ?key= (CIN-111).
        raise VideoStudioFailedError(f"Gemini API {response.status_code}: {response.text[:300]}")
    body = response.json()
    raw = "".join(
        part["text"]
        for part in body["candidates"][0]["content"]["parts"]
        if "text" in part
    )
    return parse_ideas(raw)
