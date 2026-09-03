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
        "preview_topic": "новая модель беспроводных наушников",
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
        "preview_topic": "утренний кофе в городской кофейне",
        "description": "Тема или продукт в живой обстановке, с человеком и контекстом.",
        "directive": (
            "Template: lifestyle scene. The subject appears naturally in a real "
            "everyday setting with a person mid-action; candid documentary feel, "
            "warm natural light, believable environment details, no staged posing."
        ),
    },
    "text_card": {
        "title": "Карточка с текстом",
        "preview_topic": "цитата о том, что дисциплина важнее вдохновения",
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
        "preview_topic": "открытие студии подкастов в пятницу вечером",
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
        "preview_topic": "рабочий стол до и после уборки",
        "description": "Сравнение двух состояний одного объекта в одном кадре.",
        "directive": (
            "Template: before/after comparison. A single frame split into two "
            "clearly separated halves showing the same subject in two states; "
            "identical framing and lighting on both sides so only the change "
            "stands out; a thin clean divider between the halves."
        ),
    },
    # CIN-150: second wave -- the five above covered the obvious cases,
    # these fill the gaps SMM work actually runs into (flat lays for
    # product roundups, macro for texture/quality claims, workspace and
    # interior shots for "behind the scenes", a portrait for social
    # proof, a diagram for explainers).
    "minimal_backdrop": {
        "title": "Минимализм",
        "preview_topic": "керамическая кружка ручной работы",
        "description": "Объект на однотонном фоне с большим свободным полем под текст.",
        "directive": (
            "Template: minimal backdrop. A single small subject placed off-center "
            "against a large expanse of one flat muted color; generous empty space "
            "occupying most of the frame; soft even light, minimal shadow, no props."
        ),
    },
    "flat_lay": {
        "title": "Флэтлей",
        "preview_topic": "набор для каллиграфии: перья, чернила, бумага",
        "description": "Несколько предметов сверху, аккуратной раскладкой.",
        "directive": (
            "Template: flat lay. Several related objects arranged on a flat surface "
            "and shot straight down from directly above; even diffused daylight, "
            "deliberate spacing and alignment between objects, consistent color story."
        ),
    },
    "macro_detail": {
        "title": "Макро-деталь",
        "preview_topic": "фактура обжаренного кофейного зерна",
        "description": "Крупный план фактуры или детали продукта.",
        "directive": (
            "Template: macro detail. Extreme close-up of one texture or component "
            "filling the frame; very shallow depth of field with a sharp focal point, "
            "raking light that reveals surface texture, no full object visible."
        ),
    },
    "workspace": {
        "title": "Рабочий процесс",
        "preview_topic": "мастер собирает деревянную полку",
        "description": "Руки за работой: съёмка процесса, а не результата.",
        "directive": (
            "Template: work in progress. Hands mid-task at a real workspace, shot "
            "from a slight overhead angle; tools and materials in natural disarray, "
            "warm practical light, motion implied rather than frozen posing; no face."
        ),
    },
    "testimonial_portrait": {
        "title": "Портрет-отзыв",
        "preview_topic": "довольная владелица небольшого магазина",
        "description": "Портрет человека с местом под цитату рядом.",
        "directive": (
            "Template: testimonial portrait. One person from the chest up, positioned "
            "to one side of the frame and looking slightly off-camera with a natural "
            "expression; soft directional light, uncluttered background, the opposite "
            "half of the frame left clear for a quote."
        ),
    },
    "venue": {
        "title": "Локация",
        "preview_topic": "уютный зал книжного магазина с большими окнами",
        "description": "Интерьер или место: атмосфера пространства.",
        "directive": (
            "Template: venue shot. A wide view of an interior or location conveying "
            "its atmosphere; natural light from a visible window or opening, depth "
            "from foreground and background layers, people absent or small and blurred."
        ),
    },
    "diagram": {
        "title": "Схема",
        "preview_topic": "три шага запуска рекламной кампании",
        "description": "Простая инфографика: шаги или связи между блоками.",
        "directive": (
            "Template: explanatory diagram. A clean flat vector-style illustration of "
            "three to five labelled blocks connected by arrows on a plain background; "
            "one accent color plus neutrals, generous whitespace, no photorealism, "
            "and no decorative clutter."
        ),
    },
}
