from app.social_integrations.text_formatting import (
    strip_markdown,
    to_telegram_markdown_v2,
)


def test_to_telegram_markdown_v2_converts_double_asterisk_to_single() -> None:
    assert to_telegram_markdown_v2("**жирный**") == "*жирный*"


def test_to_telegram_markdown_v2_escapes_special_chars_outside_bold() -> None:
    result = to_telegram_markdown_v2("Осень. Скидка 20%! Успей — купи.")
    assert result == "Осень\\. Скидка 20%\\! Успей — купи\\."


def test_to_telegram_markdown_v2_escapes_special_chars_inside_bold() -> None:
    result = to_telegram_markdown_v2("**Скидка 20%!**")
    assert result == "*Скидка 20%\\!*"


def test_to_telegram_markdown_v2_mixed_plain_and_bold() -> None:
    result = to_telegram_markdown_v2("Новинка: **осенний латте**. Уже в продаже!")
    assert result == "Новинка: *осенний латте*\\. Уже в продаже\\!"


def test_to_telegram_markdown_v2_handles_multiple_bold_spans() -> None:
    result = to_telegram_markdown_v2("**раз** и **два**")
    assert result == "*раз* и *два*"


def test_to_telegram_markdown_v2_stray_single_asterisk_is_escaped_not_bold() -> None:
    # An unpaired '*' isn't a bold marker (no closing **) -- must be
    # escaped like any other literal special character, not left raw
    # (which would break MarkdownV2 entity parsing).
    result = to_telegram_markdown_v2("2 * 2 = 4")
    assert result == "2 \\* 2 \\= 4"


def test_to_telegram_markdown_v2_no_markdown_is_fully_escaped() -> None:
    result = to_telegram_markdown_v2("Привет, мир!")
    assert result == "Привет, мир\\!"
    result2 = to_telegram_markdown_v2("Цена: $10.99 (скидка -5%)")
    assert "\\." in result2 and "\\(" in result2 and "\\)" in result2 and "\\-" in result2


def test_strip_markdown_removes_double_asterisk_bold() -> None:
    assert strip_markdown("**жирный текст**") == "жирный текст"


def test_strip_markdown_removes_single_asterisk_italic() -> None:
    assert strip_markdown("это *курсив* тут") == "это курсив тут"


def test_strip_markdown_removes_underscore_italic() -> None:
    assert strip_markdown("это _курсив_ тут") == "это курсив тут"


def test_strip_markdown_removes_backtick_code() -> None:
    assert strip_markdown("используй `код` тут") == "используй код тут"


def test_strip_markdown_leaves_plain_text_untouched() -> None:
    assert strip_markdown("Обычный текст без разметки.") == "Обычный текст без разметки."


def test_strip_markdown_mixed() -> None:
    result = strip_markdown("**Осенний латте** уже в продаже! Попробуй `промокод` OSEN.")
    assert result == "Осенний латте уже в продаже! Попробуй промокод OSEN."
