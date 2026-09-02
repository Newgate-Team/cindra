"""Pillow renderer for the laid-out image templates (CIN-148).

Everything here is deterministic and offline: no model call, no API
key, no network except fetching a user's own background image from our
own bucket. That is the whole point -- text lands exactly as typed,
which the image models cannot promise (CIN-125).
"""

import io
import os
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageDraw, ImageFont

from app.config import get_settings
from app.layout_templates import (
    CANVAS_FORMATS,
    DEFAULT_THEME,
    LAYOUT_TEMPLATES,
    THEMES,
)

# Bundled with Debian's fonts-dejavu-core (installed in the Dockerfile)
# -- full Cyrillic coverage, unlike Pillow's built-in default font,
# which renders Russian as empty boxes (verified before choosing it).
# The macOS entries only matter for local development.
_FONT_CANDIDATES = {
    "regular": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ),
    "bold": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
}


class LayoutFontMissingError(RuntimeError):
    """No usable font on this machine. Surfaces as a 503 the same way a
    missing GOOGLE_CLIENT_ID does (CIN-133) -- a deployment problem to
    fix, not something the user can retry into working."""


class LayoutRenderError(ValueError):
    """Bad input for a render: unknown template/format/theme, a missing
    required slot, or a background image that isn't ours."""


def _font_path(weight: str) -> str:
    settings = get_settings()
    configured = (
        settings.layout_font_bold if weight == "bold" else settings.layout_font_regular
    )
    candidates = (configured, *_FONT_CANDIDATES[weight]) if configured else _FONT_CANDIDATES[weight]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    raise LayoutFontMissingError(
        "Не найден шрифт для рендера шаблонов. Установите fonts-dejavu-core "
        "или задайте LAYOUT_FONT_REGULAR/LAYOUT_FONT_BOLD."
    )


@lru_cache(maxsize=64)
def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    # Cached: a single render fits text by trying many sizes, and
    # re-parsing the .ttf for each attempt dominates the runtime.
    return ImageFont.truetype(_font_path(weight), size)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Greedy word wrap. A single word longer than the box is left to
    overflow rather than hyphenated blindly -- the caller shrinks the
    size instead, which looks better than a broken word."""
    lines: list[str] = []
    for paragraph in text.splitlines():
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        line = words[0]
        for word in words[1:]:
            candidate = f"{line} {word}"
            if font.getbbox(candidate)[2] <= max_width:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def _fit_text(
    text: str, weight: str, box: tuple[int, int], max_size: int, min_size: int, line_spacing: float
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Largest size between min and max at which the wrapped text fits
    the box. Falls back to min_size (accepting overflow) so an
    over-long input still renders instead of failing the request --
    slot max_length keeps that case rare."""
    box_width, box_height = box
    size = max_size
    while size > min_size:
        font = _font(weight, size)
        lines = _wrap(text, font, box_width)
        if len(lines) * font.size * line_spacing <= box_height:
            return font, lines
        size -= max(1, size // 20)
    font = _font(weight, min_size)
    return font, _wrap(text, font, box_width)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise LayoutRenderError(f"Некорректный цвет: #{value}")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _palette(theme_name: str, accent: str | None) -> dict[str, tuple[int, int, int]]:
    theme = THEMES.get(theme_name)
    if theme is None:
        raise LayoutRenderError(
            f"Неизвестная тема: {theme_name}. Доступные: {', '.join(sorted(THEMES))}"
        )
    palette = {key: _hex_to_rgb(value) for key, value in theme.items()}
    if accent:
        palette["accent"] = _hex_to_rgb(accent)
    # Text over a photo ignores the theme: the scrim below it is always
    # dark, so light text is the only readable choice.
    palette["on_image"] = (255, 255, 255)
    palette["on_image_muted"] = (222, 218, 214)
    return palette


def _validate_background_url(url: str) -> None:
    """Backgrounds may only come from our own public bucket -- the same
    rule TikTok publishing follows (CIN-134), so this endpoint can't be
    turned into an SSRF proxy to arbitrary hosts."""
    allowed_base = get_settings().r2_public_url_base.rstrip("/")
    parsed = urlparse(url)
    if not allowed_base or parsed.scheme != "https" or not url.startswith(f"{allowed_base}/"):
        raise LayoutRenderError(
            "Фоновое изображение можно взять только из медиа-хранилища Cindra"
        )


def _load_background(
    url: str, canvas: tuple[int, int], client: httpx.Client | None = None
) -> Image.Image:
    _validate_background_url(url)
    get = client.get if client is not None else httpx.get
    response = get(url, timeout=30.0, follow_redirects=False)
    if response.status_code != 200:
        raise LayoutRenderError(f"Не удалось загрузить фоновое изображение: HTTP {response.status_code}")
    source = Image.open(io.BytesIO(response.content)).convert("RGB")

    # cover-fit: fill the canvas, crop the overflow, never letterbox
    canvas_w, canvas_h = canvas
    scale = max(canvas_w / source.width, canvas_h / source.height)
    resized = source.resize((max(1, round(source.width * scale)), max(1, round(source.height * scale))))
    left = (resized.width - canvas_w) // 2
    top = (resized.height - canvas_h) // 2
    return resized.crop((left, top, left + canvas_w, top + canvas_h))


def render_layout(
    template_id: str,
    canvas_format: str,
    values: dict[str, str],
    theme: str = DEFAULT_THEME,
    accent: str | None = None,
    background_url: str | None = None,
    client: httpx.Client | None = None,
) -> bytes:
    """Render one template to PNG bytes.

    `values` maps slot name -> text; missing optional slots are simply
    skipped, so the same template works with or without an author line.
    """
    template = LAYOUT_TEMPLATES.get(template_id)
    if template is None:
        raise LayoutRenderError(
            f"Неизвестный шаблон: {template_id}. Доступные: {', '.join(sorted(LAYOUT_TEMPLATES))}"
        )
    canvas = CANVAS_FORMATS.get(canvas_format)
    if canvas is None:
        raise LayoutRenderError(
            f"Неизвестный формат: {canvas_format}. Доступные: {', '.join(sorted(CANVAS_FORMATS))}"
        )
    for slot in template["slots"]:
        if slot["required"] and not (values.get(slot["name"]) or "").strip():
            raise LayoutRenderError(f"Не заполнено обязательное поле: {slot['label']}")

    palette = _palette(theme, accent)
    width, height = canvas
    if background_url:
        if not template["supports_image"]:
            raise LayoutRenderError(f"Шаблон «{template['title']}» не использует фоновое изображение")
        image = _load_background(background_url, canvas, client=client)
    else:
        image = Image.new("RGB", canvas, palette["bg"])
    draw = ImageDraw.Draw(image)

    # Font sizes scale off the canvas height so a template keeps its
    # proportions in every format instead of looking tiny in stories.
    for block in template["blocks"]:
        if block["type"] == "scrim":
            if background_url is None:
                continue
            overlay = Image.new("RGBA", canvas, (0, 0, 0, int(255 * block["opacity"])))
            image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
            draw = ImageDraw.Draw(image)
            continue

        if block["type"] == "rect":
            x0 = round(block["left"] * width)
            y0 = round(block["top"] * height)
            draw.rectangle(
                [x0, y0, x0 + round(block["width"] * width), y0 + round(block["height"] * height)],
                fill=palette[block["color"]],
            )
            continue

        text = (values.get(block["slot"]) or "").strip()
        if not text:
            continue
        if block.get("uppercase"):
            text = text.upper()
        line_spacing = block.get("line_spacing", 1.2)
        font, lines = _fit_text(
            text,
            block["weight"],
            (round(block["width"] * width), round(block["height"] * height)),
            round(block["max_size"] * height),
            round(block["min_size"] * height),
            line_spacing,
        )
        x = round(block["left"] * width)
        y = round(block["top"] * height)
        for line in lines:
            draw.text((x, y), line, font=font, fill=palette[block["color"]])
            y += round(font.size * line_spacing)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_sample(template_id: str, canvas_format: str = "square", theme: str = DEFAULT_THEME) -> bytes:
    """Gallery preview: the template filled with demo copy. Costs
    nothing but CPU, so the UI can show what every template looks like
    without an API key -- unlike the AI templates of CIN-143, whose
    previews need a real generation."""
    from app.layout_templates import SAMPLE_VALUES

    template = LAYOUT_TEMPLATES.get(template_id)
    if template is None:
        raise LayoutRenderError(f"Неизвестный шаблон: {template_id}")
    values = {slot["name"]: SAMPLE_VALUES.get(slot["name"], slot["label"]) for slot in template["slots"]}
    return render_layout(template_id, canvas_format, values, theme=theme)


def available_slots(template_id: str) -> list[dict[str, Any]]:
    return list(LAYOUT_TEMPLATES[template_id]["slots"])
