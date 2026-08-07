from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

# Instagram's Content Publishing API has no text-overlay/caption
# parameter for Stories -- a Story container only ever accepts
# image_url/video_url (confirmed against Meta's own docs, CIN-123).
# The only way generated text ends up visible on a published Story is
# rendering it directly onto the image before upload, which is what
# this module does.

_BAR_OPACITY = 140  # 0-255, semi-transparent so the photo stays visible
_BAR_MARGIN_RATIO = 0.08  # gap from the bottom edge, clear of Instagram's own UI chrome
_TEXT_MARGIN_RATIO = 0.06  # horizontal padding the text wraps within
_FONT_SIZE_RATIO = 0.052  # font size relative to image width


def _wrap_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def composite_story_text(image_bytes: bytes, text: str) -> bytes:
    """Renders `text` onto `image_bytes` as a centered, bottom-aligned
    bar and returns new PNG-encoded bytes (regardless of input format).
    """
    image = Image.open(BytesIO(image_bytes)).convert("RGBA")
    width, height = image.size

    font_size = max(24, round(width * _FONT_SIZE_RATIO))
    font = ImageFont.load_default(size=font_size)

    draw = ImageDraw.Draw(image)
    text_margin = round(width * _TEXT_MARGIN_RATIO)
    lines = _wrap_lines(draw, text, font, width - 2 * text_margin)

    line_height = draw.textbbox((0, 0), "Ag", font=font)[3]
    line_spacing = round(line_height * 0.3)
    bar_padding = round(font_size * 0.8)
    block_height = len(lines) * line_height + max(0, len(lines) - 1) * line_spacing
    bar_height = block_height + 2 * bar_padding
    bar_bottom = height - round(height * _BAR_MARGIN_RATIO)
    bar_top = bar_bottom - bar_height

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle([0, bar_top, width, bar_bottom], fill=(0, 0, 0, _BAR_OPACITY))

    y = bar_top + bar_padding
    for line in lines:
        line_width = draw.textlength(line, font=font)
        x = round((width - line_width) / 2)
        overlay_draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_height + line_spacing

    composited = Image.alpha_composite(image, overlay).convert("RGB")
    buffer = BytesIO()
    composited.save(buffer, format="PNG")
    return buffer.getvalue()
