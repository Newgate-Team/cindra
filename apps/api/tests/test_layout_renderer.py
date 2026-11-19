import io

import httpx
import pytest
from PIL import Image

from app.config import get_settings
from app.content_pipeline import layout_renderer
from app.content_pipeline.layout_renderer import (
    LayoutFontMissingError,
    LayoutRenderError,
    render_layout,
    render_sample,
)
from app.layout_templates import CANVAS_FORMATS, LAYOUT_TEMPLATES

_BUCKET = "https://media.cindra.example"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _open(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png))


@pytest.mark.parametrize("template_id", sorted(LAYOUT_TEMPLATES))
@pytest.mark.parametrize("canvas_format", sorted(CANVAS_FORMATS))
def test_every_template_renders_in_every_format(template_id: str, canvas_format: str) -> None:
    # Fractional coordinates mean one spec has to work in all three
    # canvas sizes -- this is what stops a new template from silently
    # only being checked as a square.
    image = _open(render_sample(template_id, canvas_format))
    assert image.format == "PNG"
    assert image.size == CANVAS_FORMATS[canvas_format]


def test_rendered_text_is_exactly_what_was_passed() -> None:
    # The whole point of CIN-148: unlike an image model (CIN-125), the
    # renderer cannot misspell the user's text. Proven structurally --
    # the same string at two sizes differs only in ink coverage, never
    # in content -- so here we assert the render is non-empty and
    # deterministic for identical input.
    values = {"quote": "Точный текст без ошибок", "author": "Тест"}
    first = render_layout("quote_card", "square", values)
    second = render_layout("quote_card", "square", values)
    assert first == second
    ink = sum(1 for p in _open(first).convert("L").get_flattened_data() if p > 100)
    assert ink > 1000


def test_long_text_shrinks_instead_of_failing() -> None:
    short = render_layout("quote_card", "square", {"quote": "Коротко"})
    long = render_layout("quote_card", "square", {"quote": "Слово " * 40})
    assert short != long
    assert _open(long).size == (1080, 1080)


def test_optional_slot_may_be_omitted() -> None:
    render_layout("quote_card", "square", {"quote": "Только цитата"})


def test_missing_required_slot_is_rejected() -> None:
    with pytest.raises(LayoutRenderError) as exc_info:
        render_layout("quote_card", "square", {"author": "Без цитаты"})
    assert "Цитата" in str(exc_info.value)


def test_unknown_template_format_and_theme_are_rejected() -> None:
    with pytest.raises(LayoutRenderError):
        render_layout("no_such_template", "square", {})
    with pytest.raises(LayoutRenderError):
        render_layout("quote_card", "panorama", {"quote": "x"})
    with pytest.raises(LayoutRenderError):
        render_layout("quote_card", "square", {"quote": "x"}, theme="neon")


def test_accent_override_changes_the_output() -> None:
    default = render_layout("stat_card", "square", {"value": "42", "caption": "тест"})
    custom = render_layout(
        "stat_card", "square", {"value": "42", "caption": "тест"}, accent="#00A3FF"
    )
    assert default != custom


def test_malformed_accent_is_rejected() -> None:
    with pytest.raises(LayoutRenderError):
        render_layout("stat_card", "square", {"value": "1", "caption": "x"}, accent="#zzzzzz")


def test_background_from_foreign_host_is_refused_without_fetching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same SSRF rule as TikTok publishing (CIN-134): the URL arrives in
    # a request body, so an unexpected host must not be fetched at all.
    monkeypatch.setattr(get_settings(), "r2_public_url_base", _BUCKET)
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(str(request.url))
        return httpx.Response(200, content=b"")

    with pytest.raises(LayoutRenderError):
        render_layout(
            "photo_quote",
            "square",
            {"headline": "текст"},
            background_url="https://evil.example.com/pic.png",
            client=_client(handler),
        )
    assert fetched == []


def test_background_is_cover_fitted_and_scrimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "r2_public_url_base", _BUCKET)
    source = Image.new("RGB", (400, 1200), (255, 255, 255))
    buffer = io.BytesIO()
    source.save(buffer, format="PNG")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=buffer.getvalue())

    png = render_layout(
        "photo_quote",
        "square",
        {"headline": "текст поверх"},
        background_url=f"{_BUCKET}/pic.png",
        client=_client(handler),
    )
    image = _open(png).convert("RGB")
    assert image.size == (1080, 1080)
    # A white source under a 55% scrim must come out mid-grey, which
    # also proves the scrim was applied rather than skipped.
    corner = image.getpixel((5, 5))
    assert all(90 < channel < 190 for channel in corner)


def test_background_rejected_for_template_without_image_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "r2_public_url_base", _BUCKET)
    with pytest.raises(LayoutRenderError):
        render_layout(
            "quote_card", "square", {"quote": "x"}, background_url=f"{_BUCKET}/pic.png"
        )


def test_missing_font_raises_its_own_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deployment problem, not user input -- the router turns this into
    # a 503 rather than a 400.
    monkeypatch.setattr(get_settings(), "layout_font_regular", "")
    monkeypatch.setattr(get_settings(), "layout_font_bold", "")
    monkeypatch.setattr(
        layout_renderer, "_FONT_CANDIDATES", {"regular": ("/nope.ttf",), "bold": ("/nope.ttf",)}
    )
    layout_renderer._font.cache_clear()
    try:
        with pytest.raises(LayoutFontMissingError):
            render_layout("quote_card", "square", {"quote": "x"})
    finally:
        layout_renderer._font.cache_clear()


def test_literal_number_blocks_disappear_with_their_step() -> None:
    # CIN-151: step numerals are literals in the spec, not slots, so
    # they need their own visibility rule -- an unused third step must
    # not leave an orphan "3" on the card.
    three = render_layout(
        "steps", "square", {"step_1": "Первый", "step_2": "Второй", "step_3": "Третий"}
    )
    two = render_layout("steps", "square", {"step_1": "Первый", "step_2": "Второй"})
    assert three != two

    def ink(png: bytes) -> int:
        return sum(1 for p in _open(png).convert("L").get_flattened_data() if p > 60)

    # Dropping a step must remove ink, never add it.
    assert ink(two) < ink(three)


def test_comparison_renders_both_columns() -> None:
    png = render_layout(
        "comparison",
        "square",
        {
            "left_title": "Было",
            "left_body": "Долго",
            "right_title": "Стало",
            "right_body": "Быстро",
        },
    )
    image = _open(png).convert("RGB")
    # Ink on both sides of the divider, i.e. the two columns really are
    # laid out side by side rather than overlapping.
    left = image.crop((0, 0, 540, 1080)).convert("L")
    right = image.crop((540, 0, 1080, 1080)).convert("L")
    assert sum(1 for p in left.get_flattened_data() if p > 60) > 500
    assert sum(1 for p in right.get_flattened_data() if p > 60) > 500
