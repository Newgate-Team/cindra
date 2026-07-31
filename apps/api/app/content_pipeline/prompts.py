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
}

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
) -> str:
    """Build the user-message prompt for text generation.

    `content_kind` is one of "post" / "story" / "video_script" (falls
    back to plain "Обычный пост." guidance for anything else, rather
    than raising -- an unrecognized kind shouldn't crash the request).
    """
    lines = [
        f"Тема: {topic}",
        _PLATFORM_GUIDANCE[platform],
        _CONTENT_KIND_GUIDANCE.get(content_kind, _CONTENT_KIND_GUIDANCE["post"]),
    ]
    if brand_guide:
        lines.append(f"Бренд-гайд (соблюдать тон и стиль): {brand_guide}")
    lines.append("Верни только готовый текст, без пояснений и без обрамляющих кавычек.")
    return "\n".join(lines)
