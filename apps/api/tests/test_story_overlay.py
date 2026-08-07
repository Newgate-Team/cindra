from io import BytesIO

from PIL import Image

from app.content_pipeline.story_overlay import _wrap_lines, composite_story_text


def _solid_png(width: int = 400, height: int = 700, color: tuple[int, int, int] = (30, 30, 30)) -> bytes:
    image = Image.new("RGB", (width, height), color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_output_is_a_valid_png_with_unchanged_dimensions() -> None:
    result = composite_story_text(_solid_png(400, 700), "Свежий кофе")
    image = Image.open(BytesIO(result))
    assert image.format == "PNG"
    assert image.size == (400, 700)


def test_bottom_bar_area_differs_from_the_original_solid_color() -> None:
    color = (30, 30, 30)
    original = _solid_png(400, 700, color)
    result = composite_story_text(original, "Свежий кофе")
    image = Image.open(BytesIO(result)).convert("RGB")

    # Somewhere in the bottom quarter (where the bar is drawn), at
    # least one pixel must no longer be the original flat color.
    width, height = image.size
    region = image.crop((0, height - height // 4, width, height))
    assert set(region.getdata()) != {color}


def test_top_of_the_image_is_left_untouched() -> None:
    color = (30, 30, 30)
    original = _solid_png(400, 700, color)
    result = composite_story_text(original, "Свежий кофе")
    image = Image.open(BytesIO(result)).convert("RGB")

    width, height = image.size
    region = image.crop((0, 0, width, height // 3))
    assert set(region.getdata()) == {color}


def test_input_format_other_than_png_is_still_accepted() -> None:
    image = Image.new("RGB", (400, 700), (10, 20, 30))
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    result = composite_story_text(buffer.getvalue(), "Свежий кофе")
    assert Image.open(BytesIO(result)).format == "PNG"


def test_wrap_lines_splits_long_text_across_multiple_lines() -> None:
    from PIL import ImageDraw, ImageFont

    image = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=40)

    lines = _wrap_lines(draw, "Свежий утренний кофе для всех", font, max_width=150)
    assert len(lines) > 1
    assert " ".join(lines).replace("  ", " ") == "Свежий утренний кофе для всех"


def test_wrap_lines_keeps_short_text_on_one_line() -> None:
    from PIL import ImageDraw, ImageFont

    image = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=40)

    lines = _wrap_lines(draw, "Кофе", font, max_width=800)
    assert lines == ["Кофе"]
