"""Laid-out image templates rendered by code, not by a model (CIN-148).

Image models garble longer on-image text (CIN-125 saw real production
photos with misspelled Russian baked in) and lay it out differently
every run. For a quote card or a stat card that is unacceptable, so
these templates put the user's text on the canvas exactly as typed.

A template is pure data: `blocks` are drawn in order, and every
coordinate is a fraction of the canvas, so one spec renders correctly
in every format below. `slots` are what the UI asks the user to fill.

Adding a template is a change to this file only -- no renderer changes
and no migration (`template_id` is validated at the API boundary and
stored nowhere).
"""

from typing import Any

# Width/height in pixels per canvas format. 1080 wide matches what
# Instagram and TikTok expect; the landscape one is sized for
# Telegram/Facebook link-style posts.
CANVAS_FORMATS: dict[str, tuple[int, int]] = {
    "square": (1080, 1080),
    "story": (1080, 1920),
    "landscape": (1200, 675),
}

# Converted from the web app's oklch design tokens in
# apps/web/app/globals.css, so a rendered card sits in the same palette
# as the product itself rather than a second, hand-picked one.
THEMES: dict[str, dict[str, str]] = {
    "dark": {"bg": "#0F0D0B", "fg": "#F3F1EF", "muted": "#9D9791", "accent": "#DA2E2B"},
    "light": {"bg": "#F3F1EF", "fg": "#0F0D0B", "muted": "#5B5652", "accent": "#DA2E2B"},
    "ember": {"bg": "#DA2E2B", "fg": "#FFFFFF", "muted": "#FFD9D2", "accent": "#0F0D0B"},
}
DEFAULT_THEME = "dark"

# Sample text used by the preview endpoint -- the gallery has to show
# what a template looks like before the user has typed anything.
SAMPLE_VALUES: dict[str, str] = {
    "quote": "Хороший контент не кричит. Он попадает в то, о чём человек уже думал.",
    "author": "Аня Соколова, редактор",
    "statement": "Публикуйте реже. Готовьте лучше.",
    "left_title": "Было",
    "left_body": "Три часа на пост: идея, текст, картинка, вычитка",
    "right_title": "Стало",
    "right_body": "Двадцать минут: тема, шаблон, публикация",
    "step_1": "Опишите тему",
    "step_2": "Выберите шаблон",
    "step_3": "Опубликуйте",
    "value": "72%",
    "caption": "постов теряют читателя на первой строке",
    "headline": "Запускаем видео-студию",
    "subhead": "Сценарий, стиль и монтажный план — за один вечер",
    "cta": "Подробности в профиле",
    "label": "Совет дня",
    "item_1": "Начните с цифры, а не с приветствия",
    "item_2": "Один пост — одна мысль",
    "item_3": "Обещание из заголовка должно прозвучать в тексте",
}


LAYOUT_TEMPLATES: dict[str, dict[str, Any]] = {
    "quote_card": {
        "title": "Цитата",
        "description": "Крупная цитата с подписью автора и акцентной чертой.",
        "supports_image": False,
        "slots": [
            {"name": "quote", "label": "Цитата", "max_length": 220, "required": True},
            {"name": "author", "label": "Автор", "max_length": 80, "required": False},
        ],
        "blocks": [
            {"type": "rect", "left": 0.08, "top": 0.16, "width": 0.10, "height": 0.010, "color": "accent"},
            {
                "type": "text", "slot": "quote", "left": 0.08, "top": 0.24, "width": 0.84, "height": 0.42,
                "max_size": 0.085, "min_size": 0.036, "weight": "bold", "color": "fg", "line_spacing": 1.22,
            },
            {
                "type": "text", "slot": "author", "left": 0.08, "top": 0.74, "width": 0.84, "height": 0.08,
                "max_size": 0.030, "min_size": 0.020, "weight": "regular", "color": "muted",
            },
        ],
    },
    "stat_card": {
        "title": "Цифра",
        "description": "Одна крупная метрика и пояснение под ней.",
        "supports_image": False,
        "slots": [
            {"name": "value", "label": "Значение", "max_length": 12, "required": True},
            {"name": "caption", "label": "Пояснение", "max_length": 140, "required": True},
            {"name": "label", "label": "Надпись сверху", "max_length": 40, "required": False},
        ],
        "blocks": [
            {
                "type": "text", "slot": "label", "left": 0.08, "top": 0.14, "width": 0.84, "height": 0.06,
                "max_size": 0.028, "min_size": 0.020, "weight": "bold", "color": "accent", "uppercase": True,
            },
            {
                "type": "text", "slot": "value", "left": 0.08, "top": 0.30, "width": 0.84, "height": 0.24,
                "max_size": 0.230, "min_size": 0.090, "weight": "bold", "color": "accent",
            },
            {
                "type": "text", "slot": "caption", "left": 0.08, "top": 0.60, "width": 0.84, "height": 0.22,
                "max_size": 0.055, "min_size": 0.028, "weight": "regular", "color": "fg", "line_spacing": 1.25,
            },
        ],
    },
    "announcement": {
        "title": "Анонс",
        "description": "Заголовок, подзаголовок и призыв к действию на плашке.",
        "supports_image": False,
        "slots": [
            {"name": "headline", "label": "Заголовок", "max_length": 90, "required": True},
            {"name": "subhead", "label": "Подзаголовок", "max_length": 160, "required": False},
            {"name": "cta", "label": "Призыв к действию", "max_length": 60, "required": False},
        ],
        "blocks": [
            {
                "type": "text", "slot": "headline", "left": 0.08, "top": 0.20, "width": 0.84, "height": 0.30,
                "max_size": 0.100, "min_size": 0.044, "weight": "bold", "color": "fg", "line_spacing": 1.15,
            },
            {
                "type": "text", "slot": "subhead", "left": 0.08, "top": 0.54, "width": 0.84, "height": 0.16,
                "max_size": 0.046, "min_size": 0.026, "weight": "regular", "color": "muted", "line_spacing": 1.3,
            },
            {"type": "rect", "left": 0.08, "top": 0.775, "width": 0.84, "height": 0.002, "color": "accent"},
            {
                "type": "text", "slot": "cta", "left": 0.08, "top": 0.80, "width": 0.84, "height": 0.07,
                "max_size": 0.034, "min_size": 0.022, "weight": "bold", "color": "accent",
            },
        ],
    },
    "photo_quote": {
        "title": "Фото с текстом",
        "description": "Своё изображение как подложка, затемнение и текст поверх.",
        "supports_image": True,
        "slots": [
            {"name": "headline", "label": "Текст поверх фото", "max_length": 160, "required": True},
            {"name": "author", "label": "Подпись", "max_length": 80, "required": False},
        ],
        "blocks": [
            # scrim: without it light photos swallow white text entirely
            {"type": "scrim", "opacity": 0.55},
            {
                "type": "text", "slot": "headline", "left": 0.08, "top": 0.52, "width": 0.84, "height": 0.30,
                "max_size": 0.080, "min_size": 0.034, "weight": "bold", "color": "on_image", "line_spacing": 1.2,
            },
            {
                "type": "text", "slot": "author", "left": 0.08, "top": 0.85, "width": 0.84, "height": 0.06,
                "max_size": 0.028, "min_size": 0.020, "weight": "regular", "color": "on_image_muted",
            },
        ],
    },
    "tip_list": {
        "title": "Список советов",
        "description": "Заголовок и до трёх пунктов с акцентными маркерами.",
        "supports_image": False,
        "slots": [
            {"name": "headline", "label": "Заголовок", "max_length": 70, "required": True},
            {"name": "item_1", "label": "Пункт 1", "max_length": 90, "required": True},
            {"name": "item_2", "label": "Пункт 2", "max_length": 90, "required": False},
            {"name": "item_3", "label": "Пункт 3", "max_length": 90, "required": False},
        ],
        "blocks": [
            {
                "type": "text", "slot": "headline", "left": 0.08, "top": 0.13, "width": 0.84, "height": 0.17,
                "max_size": 0.068, "min_size": 0.036, "weight": "bold", "color": "fg", "line_spacing": 1.15,
            },
            {"type": "rect", "left": 0.08, "top": 0.395, "width": 0.035, "height": 0.008, "color": "accent"},
            {
                "type": "text", "slot": "item_1", "left": 0.15, "top": 0.375, "width": 0.77, "height": 0.13,
                "max_size": 0.042, "min_size": 0.024, "weight": "regular", "color": "fg", "line_spacing": 1.25,
            },
            {"type": "rect", "left": 0.08, "top": 0.565, "width": 0.035, "height": 0.008, "color": "accent"},
            {
                "type": "text", "slot": "item_2", "left": 0.15, "top": 0.545, "width": 0.77, "height": 0.13,
                "max_size": 0.042, "min_size": 0.024, "weight": "regular", "color": "fg", "line_spacing": 1.25,
            },
            {"type": "rect", "left": 0.08, "top": 0.735, "width": 0.035, "height": 0.008, "color": "accent"},
            {
                "type": "text", "slot": "item_3", "left": 0.15, "top": 0.715, "width": 0.77, "height": 0.13,
                "max_size": 0.042, "min_size": 0.024, "weight": "regular", "color": "fg", "line_spacing": 1.25,
            },
        ],
    },
    # CIN-151: three more shapes the first five didn't cover -- a bare
    # statement, a two-column comparison, and a numbered sequence.
    "statement": {
        "title": "Тезис",
        "description": "Одна короткая фраза во весь кадр, без ничего лишнего.",
        "supports_image": False,
        "slots": [
            {"name": "statement", "label": "Фраза", "max_length": 90, "required": True},
        ],
        "blocks": [
            {
                "type": "text", "slot": "statement", "left": 0.08, "top": 0.28, "width": 0.84, "height": 0.44,
                "max_size": 0.130, "min_size": 0.050, "weight": "bold", "color": "fg", "line_spacing": 1.15,
            },
            {"type": "rect", "left": 0.08, "top": 0.80, "width": 0.14, "height": 0.012, "color": "accent"},
        ],
    },
    "comparison": {
        "title": "Было / стало",
        "description": "Две колонки рядом: старый подход и новый.",
        "supports_image": False,
        "slots": [
            {"name": "left_title", "label": "Заголовок слева", "max_length": 30, "required": True},
            {"name": "left_body", "label": "Текст слева", "max_length": 120, "required": True},
            {"name": "right_title", "label": "Заголовок справа", "max_length": 30, "required": True},
            {"name": "right_body", "label": "Текст справа", "max_length": 120, "required": True},
        ],
        "blocks": [
            {
                "type": "text", "slot": "left_title", "left": 0.08, "top": 0.24, "width": 0.36, "height": 0.09,
                "max_size": 0.055, "min_size": 0.030, "weight": "bold", "color": "muted",
            },
            {
                "type": "text", "slot": "left_body", "left": 0.08, "top": 0.36, "width": 0.36, "height": 0.30,
                "max_size": 0.042, "min_size": 0.022, "weight": "regular", "color": "fg", "line_spacing": 1.25,
            },
            # the divider doubles as the visual "versus"
            {"type": "rect", "left": 0.494, "top": 0.24, "width": 0.004, "height": 0.42, "color": "accent"},
            {
                "type": "text", "slot": "right_title", "left": 0.56, "top": 0.24, "width": 0.36, "height": 0.09,
                "max_size": 0.055, "min_size": 0.030, "weight": "bold", "color": "accent",
            },
            {
                "type": "text", "slot": "right_body", "left": 0.56, "top": 0.36, "width": 0.36, "height": 0.30,
                "max_size": 0.042, "min_size": 0.022, "weight": "regular", "color": "fg", "line_spacing": 1.25,
            },
        ],
    },
    "steps": {
        "title": "Три шага",
        "description": "Пронумерованная последовательность действий.",
        "supports_image": False,
        "slots": [
            {"name": "headline", "label": "Заголовок", "max_length": 60, "required": False},
            {"name": "step_1", "label": "Шаг 1", "max_length": 70, "required": True},
            {"name": "step_2", "label": "Шаг 2", "max_length": 70, "required": True},
            {"name": "step_3", "label": "Шаг 3", "max_length": 70, "required": False},
        ],
        "blocks": [
            {
                "type": "text", "slot": "headline", "left": 0.08, "top": 0.13, "width": 0.84, "height": 0.12,
                "max_size": 0.060, "min_size": 0.032, "weight": "bold", "color": "fg", "line_spacing": 1.15,
            },
            # Literal numerals, not slots -- and each vanishes with its
            # own step so an unused third one leaves no orphan "3".
            {
                "type": "text", "text": "1", "left": 0.08, "top": 0.365, "width": 0.09, "height": 0.10,
                "max_size": 0.070, "min_size": 0.070, "weight": "bold", "color": "accent",
            },
            {
                "type": "text", "slot": "step_1", "left": 0.19, "top": 0.375, "width": 0.73, "height": 0.12,
                "max_size": 0.046, "min_size": 0.026, "weight": "regular", "color": "fg", "line_spacing": 1.2,
            },
            {
                "type": "text", "text": "2", "left": 0.08, "top": 0.535, "width": 0.09, "height": 0.10,
                "max_size": 0.070, "min_size": 0.070, "weight": "bold", "color": "accent",
                "visible_with": "step_2",
            },
            {
                "type": "text", "slot": "step_2", "left": 0.19, "top": 0.545, "width": 0.73, "height": 0.12,
                "max_size": 0.046, "min_size": 0.026, "weight": "regular", "color": "fg", "line_spacing": 1.2,
            },
            {
                "type": "text", "text": "3", "left": 0.08, "top": 0.705, "width": 0.09, "height": 0.10,
                "max_size": 0.070, "min_size": 0.070, "weight": "bold", "color": "accent",
                "visible_with": "step_3",
            },
            {
                "type": "text", "slot": "step_3", "left": 0.19, "top": 0.715, "width": 0.73, "height": 0.12,
                "max_size": 0.046, "min_size": 0.026, "weight": "regular", "color": "fg", "line_spacing": 1.2,
            },
        ],
    },
}
