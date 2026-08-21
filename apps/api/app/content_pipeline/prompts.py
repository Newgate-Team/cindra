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
    SocialPlatform.tiktok: (
        "Формат: подпись к TikTok-видео. Начни с короткого хука, пиши разговорно, "
        "заверши одним призывом к действию и добавь 3-5 релевантных хэштегов."
    ),
}
_DEFAULT_PLATFORM_GUIDANCE = "Формат: обычный пост в соцсети."

_CONTENT_KIND_GUIDANCE = {
    "post": "Обычный пост.",
    "story": "Сторис: короче, разговорнее, с одним явным призывом к действию.",
    # CIN-132: expanded from a bare "покадрово, с таймкодами и репликами" --
    # grounded in the run-social-content skill's create-viral-short-videos
    # reference (pattern-library.md's hook families + evidence-base.md's
    # v1 hook associations: concrete numbers/diagnosis/consequence hooks
    # modestly over-index vs. hype adjectives in the calibrated corpus;
    # "first-person before evidence" under-indexes). Kept concise --
    # operative constraints only, not the underlying evidence commentary.
    "video_script": (
        "Сценарий короткого вертикального видео (Reels/Shorts/TikTok). Хук в первые "
        "1-3 секунды: конкретная цифра, точный диагноз узнаваемой проблемы или "
        "наглядный результат -- без «привет, сегодня расскажу». Дальше -- покадрово, "
        "с таймкодами и репликами, смена кадра или ракурса каждые 2-4 секунды, чтобы "
        "не терять внимание. Результат, обещанный в хуке, должен реально прозвучать "
        "в видео, а не остаться только в начале. Сценарий должен быть понятен и без "
        "звука -- закладывай текст на экране для ключевых моментов. Заверши явным "
        "призывом к действию."
    ),
}

# CIN-132: applies to every content_kind -- distilled from the
# run-social-content skill's create-social-text-posts reference
# (quality-rubric.md's common failure modes + post-patterns.md's hook
# families). Kept short by design: a few load-bearing constraints the
# model can actually follow, not the full rubric/pattern library.
_QUALITY_GUIDANCE = (
    "Стандарт качества: не начинай с приветствия или анонса темы -- сразу дай "
    "главный факт, результат или конкретную проблему. Опирайся на что-то "
    "конкретное -- цифру, механизм, пример, ограничение или точный диагноз "
    "проблемы -- вместо общих фраз вроде «раскройте потенциал» или «измените "
    "подход». Обещанная польза должна прозвучать внутри текста, а не только в "
    "заголовке. Один текст -- один посыл и не больше одного явного призыва к "
    "действию. Не выдумывай цифры, отзывы, кейсы или факты, которых нет в теме, "
    "бренд-гайде или вложениях."
)


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
        _QUALITY_GUIDANCE,
    ]
    if brand_guide:
        lines.append(f"Бренд-гайд (соблюдать тон и стиль): {brand_guide}")
    for i, text in enumerate(attachment_texts or [], start=1):
        label = "Контекст из прикреплённого документа" if len(attachment_texts) == 1 else f"Контекст из документа {i}"
        lines.append(f"{label}: {text}")
    lines.append("Верни только готовый текст, без пояснений и без обрамляющих кавычек.")
    return "\n".join(lines)
