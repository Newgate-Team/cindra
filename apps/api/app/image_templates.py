"""Image template catalog for the «Посты» page (CIN-143).

Single source of truth served to the frontend via
GET /content/image-templates -- the web app renders whatever is here,
so adding a template is a backend-only change (mirrors video_styles.py
and CIN-138's tone presets).

`directive` is English on purpose: it feeds the image prompt pipeline
(CIN-142's enhancer meta-prompt, and the fallback wrapper verbatim),
and the image model follows English art direction more reliably than
Russian. It never reaches the UI -- the frontend only sees id/title/
description.
"""

IMAGE_TEMPLATES: dict[str, dict[str, str]] = {
    "product_shot": {
        "title": "Продуктовое фото",
        "description": "Крупный план продукта на чистом фоне — карточка товара или анонс.",
        "directive": (
            "Template: product shot. One hero product fills the frame on a clean, "
            "uncluttered background; soft studio lighting with gentle shadows, "
            "shallow depth of field, crisp focus on the product's texture and "
            "branding; premium commercial photography look."
        ),
    },
    "lifestyle": {
        "title": "Лайфстайл-сцена",
        "description": "Тема или продукт в живой обстановке, с человеком и контекстом.",
        "directive": (
            "Template: lifestyle scene. The subject appears naturally in a real "
            "everyday setting with a person mid-action; candid documentary feel, "
            "warm natural light, believable environment details, no staged posing."
        ),
    },
    "text_card": {
        "title": "Карточка с текстом",
        "description": "Плашка с короткой фразой — цитата, анонс или оффер.",
        "directive": (
            "Template: text card. A clean graphic layout built around one short "
            "phrase taken from the request as the visual centerpiece: bold legible "
            "typography, generous margins, a simple flat or subtly textured "
            "background, at most one small supporting graphic element."
        ),
    },
    "event_announce": {
        "title": "Анонс события",
        "description": "Афиша: атмосфера события с местом под заголовок и дату.",
        "directive": (
            "Template: event announcement. Poster-like composition conveying the "
            "event's atmosphere: the venue or key activity as the backdrop, clear "
            "space reserved for a short title and date, energetic lighting and "
            "color accents matching the event's mood."
        ),
    },
    "before_after": {
        "title": "До/после",
        "description": "Сравнение двух состояний одного объекта в одном кадре.",
        "directive": (
            "Template: before/after comparison. A single frame split into two "
            "clearly separated halves showing the same subject in two states; "
            "identical framing and lighting on both sides so only the change "
            "stands out; a thin clean divider between the halves."
        ),
    },
}
