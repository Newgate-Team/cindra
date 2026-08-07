from app.models import SocialPlatform

_PLATFORM_GUIDANCE = {
    SocialPlatform.telegram: (
        "Формат: пост для Telegram-канала. До 1024 символов, можно использовать "
        "эмодзи по смыслу, без хэштегов в начале текста."
    ),
    SocialPlatform.instagram: (
        "Формат: подпись к посту Instagram. Первая строка — самая цепляющая (видна "
        "в свёрнутом виде), в конце — 3-5 релевантных хэштегов."
    ),
    SocialPlatform.facebook: (
        "Формат: пост на странице Facebook. Обычный абзацный текст, эмодзи уместны "
        "по смыслу, хэштеги не обязательны."
    ),
}
_DEFAULT_PLATFORM_GUIDANCE = "Формат: обычный пост в соцсети."

_CONTENT_KIND_GUIDANCE = {
    "post": "Обычный пост.",
    "story": "Сторис: короче, разговорнее, с одним явным призывом к действию.",
    "video_script": "Сценарий короткого видео: покадрово, с таймкодами и репликами.",
}


def build_text_prompt(
    topic: str,
    platform: SocialPlatform,
    content_kind: str = "post",
    brand_guide: str | None = None,
    attachment_texts: list[str] | None = None,
) -> str:
    """Build the user-message prompt for text generation.

    `content_kind` is one of "post" / "story" / "video_script" (falls
    back to plain "Обычный пост." guidance for anything else, rather
    than raising -- an unrecognized kind shouldn't crash the request).
    `attachment_texts` is extracted text from optional document
    context files (CIN-97, up to several since CIN-107) -- image/
    video/audio attachments are passed separately as multimodal parts,
    not folded into this string.
    """
    lines = [
        f"Тема: {topic}",
        _PLATFORM_GUIDANCE.get(platform, _DEFAULT_PLATFORM_GUIDANCE),
        _CONTENT_KIND_GUIDANCE.get(content_kind, _CONTENT_KIND_GUIDANCE["post"]),
    ]
    if brand_guide:
        lines.append(f"Бренд-гайд (соблюдать тон и стиль): {brand_guide}")
    for i, text in enumerate(attachment_texts or [], start=1):
        label = "Контекст из прикреплённого документа" if len(attachment_texts) == 1 else f"Контекст из документа {i}"
        lines.append(f"{label}: {text}")
    lines.append("Верни только готовый текст, без пояснений и без обрамляющих кавычек.")
    return "\n".join(lines)


def build_story_overlay_prompt(topic: str, brand_guide: str | None = None) -> str:
    """Prompt for the minimal text composited directly onto an
    Instagram Story image (CIN-123) -- deliberately much shorter than
    build_text_prompt's output: this has to read as a short line or
    two sitting on top of a photo, not a full caption."""
    lines = [
        f"Тема: {topic}",
        (
            "Очень короткая фраза для текста поверх фото в Instagram Stories -- "
            "2-6 слов, без хэштегов, без кавычек, без точки в конце. Один яркий "
            "акцент по теме, а не законченное предложение."
        ),
    ]
    if brand_guide:
        lines.append(f"Бренд-гайд (соблюдать тон): {brand_guide}")
    lines.append("Верни только саму фразу, без пояснений.")
    return "\n".join(lines)
