"""LLM calls for the video studio (CIN-135): script and brief generation.

Unlike the post pipeline (celery + GenerationJob polling, built for
minutes-long image/video work), these are synchronous text calls --
gemini-flash-lite answers in seconds, and the studio wizard needs the
result inline in the request/response cycle. The router maps
TransientGenerationError to 503 and VideoStudioFailedError to 502.
"""

import re
from typing import Any

import httpx

from app.config import get_settings
from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.prompts import _CONTENT_KIND_GUIDANCE, _QUALITY_GUIDANCE
from app.video_styles import VIDEO_STYLES

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Briefs are much longer than a social post (three files, shot lists
# with timecodes) -- give them real headroom, unlike text_generator's
# 2048 cap for single posts.
_MAX_OUTPUT_TOKENS = 8192

_FILE_MARKER_RE = re.compile(r"^===\s*FILE:\s*(\S+)\s*\|\s*(.+?)\s*===\s*$", re.MULTILINE)

# The three brief files every "brief"-producing style yields (the
# user explicitly asked for separate files: the voiceover script is
# read into a microphone as-is, so it must not be buried inside the
# production plan).
_BRIEF_FILES = (
    ("voiceover.md", "Аудио: текст для записи"),
    ("production.md", "Продакшн: что снять или сгенерировать"),
    ("edit.md", "Монтаж: сборка ролика"),
)


class VideoStudioFailedError(Exception):
    """Non-retryable Gemini failure. Deliberately not raise_for_status()
    -- that message embeds the full request URL with the ?key= query
    param (CIN-111), which must not leak into API error details."""


def _call_gemini(prompt: str, client: httpx.Client | None = None) -> str:
    settings = get_settings()
    url = f"{_GEMINI_BASE_URL}/{settings.gemini_model}:generateContent"
    request_kwargs: dict[str, Any] = {
        "params": {"key": settings.gemini_api_key},
        "headers": {"content-type": "application/json"},
        "json": {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": _MAX_OUTPUT_TOKENS},
        },
        "timeout": 60.0,
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
        raise TransientGenerationError(
            f"Gemini API {response.status_code}: {response.text[:500]}"
        )
    if response.status_code >= 400:
        raise VideoStudioFailedError(
            f"Gemini API {response.status_code}: {response.text[:500]}"
        )
    body = response.json()
    return "".join(
        part["text"]
        for part in body["candidates"][0]["content"]["parts"]
        if "text" in part
    )


def generate_script_text(
    topic: str, brand_guide: str | None = None, client: httpx.Client | None = None
) -> str:
    """Generate a short-vertical-video script for a studio project.

    Reuses the CIN-132 video_script and quality guidance verbatim so
    studio scripts and post-pipeline scripts obey the same craft rules
    (hook in 1-3s, beat cadence, on-mute legibility, single CTA).
    """
    lines = [
        f"Продукт/тема видео: {topic}",
        _CONTENT_KIND_GUIDANCE["video_script"],
        _QUALITY_GUIDANCE,
    ]
    if brand_guide:
        lines.append(f"Бренд-гайд (соблюдать тон и стиль): {brand_guide}")
    lines.append("Верни только готовый сценарий, без пояснений и без обрамляющих кавычек.")
    return _call_gemini("\n".join(lines), client=client).strip()


def _brief_prompt(topic: str, script: str, style_id: str, brand_guide: str | None) -> str:
    style = VIDEO_STYLES[style_id]
    file_list = "\n".join(
        f"=== FILE: {filename} | {title} ===" for filename, title in _BRIEF_FILES
    )
    lines = [
        ("Преврати сценарий короткого вертикального видео в производственный бриф из "
        "трёх markdown-файлов. Автор будет снимать и монтировать по этому брифу сам, "
        "без съёмочной группы."),
        f"Тема/продукт: {topic}",
        f"Сценарий:\n{script}",
        f"Выбранный стиль: {style['title']}. {style['brief_guidance']}",
        "Требования к файлам:",
        ("1. voiceover.md — чистый дикторский текст для записи, построчно, с таймкодами "
        "и пометками интонации в скобках. Никаких технических указаний — только то, "
        "что произносится вслух: этот файл читают в микрофон как есть."),
        ("2. production.md — что подготовить: пронумерованный список того, что снять "
        "или сгенерировать, в соответствии с указаниями стиля выше."),
        ("3. edit.md — монтажный план: таблица с таймкодами (что на экране, какой текст "
        "поверх, какая строка закадрового текста звучит), переходы, где музыка, "
        "финальный кадр с CTA."),
        ("Раздели файлы ровно такими маркерами (каждый на своей строке, содержимое "
        "файла — после маркера):"),
        file_list,
        "Не добавляй ничего до первого маркера и после содержимого последнего файла.",
    ]
    if brand_guide:
        lines.insert(4, f"Бренд-гайд (соблюдать тон и стиль): {brand_guide}")
    return "\n".join(lines)


def _split_brief_files(raw: str) -> list[dict[str, str]]:
    """Split the LLM output into brief files by the === FILE: === markers.

    Falls back to a single brief.md with the whole output if the model
    ignored the markers -- a degraded brief beats a 500."""
    matches = list(_FILE_MARKER_RE.finditer(raw))
    if not matches:
        return [{"filename": "brief.md", "title": "Производственный бриф", "content": raw.strip()}]
    files = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        content = raw[start:end].strip()
        if content:
            files.append(
                {"filename": match.group(1), "title": match.group(2), "content": content}
            )
    return files


def generate_brief_files(
    topic: str,
    script: str,
    style_id: str,
    brand_guide: str | None = None,
    client: httpx.Client | None = None,
) -> list[dict[str, str]]:
    raw = _call_gemini(_brief_prompt(topic, script, style_id, brand_guide), client=client)
    return _split_brief_files(raw)


# CIN-137: the production plan already contains ready generation
# prompts (the blocks/cartoon brief_guidance asks for them explicitly)
# -- this pulls them back out as data so the studio can run the image
# pipeline itself instead of the user copy-pasting into an external
# generator.
_MAX_ILLUSTRATIONS = 10

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extract_illustration_prompts(
    production_content: str, client: httpx.Client | None = None
) -> list[str]:
    """Extract the illustration-generation prompts from production.md.

    Returns at most _MAX_ILLUSTRATIONS non-empty prompt strings.
    Raises VideoStudioFailedError when the model's answer isn't a JSON
    array of strings -- a garbled list is worse than asking the user
    to regenerate the brief."""
    import json

    prompt = (
        "Ниже — производственный план короткого видео, в котором перечислены "
        "иллюстрации с готовыми промптами для генерации изображений. Верни только "
        "JSON-массив строк — по одному полному промпту на иллюстрацию, в порядке "
        "появления, без нумерации и пояснений. Если промпт написан на двух языках, "
        "возьми его целиком. Никакого текста вне JSON.\n\n"
        f"{production_content}"
    )
    raw = _call_gemini(prompt, client=client).strip()
    raw = _JSON_FENCE_RE.sub("", raw).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VideoStudioFailedError(
            "Не удалось разобрать список иллюстраций из брифа — перегенерируйте бриф"
        ) from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise VideoStudioFailedError(
            "Не удалось разобрать список иллюстраций из брифа — перегенерируйте бриф"
        )
    prompts = [item.strip() for item in parsed if item.strip()]
    if not prompts:
        raise VideoStudioFailedError(
            "В производственном плане не нашлось промптов иллюстраций"
        )
    return prompts[:_MAX_ILLUSTRATIONS]
