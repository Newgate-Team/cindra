import re

# Telegram Bot API's MarkdownV2 style requires literal occurrences of
# these characters to be backslash-escaped outside of entity markers
# (core.telegram.org/bots/api#markdownv2-style) -- unescaped, they
# either error the whole send ("can't parse entities") or start
# unintended formatting.
_MDV2_SPECIAL_CHARS = "_*[]()~`>#+-=|{}.!\\"

_BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def _escape_markdown_v2(text: str) -> str:
    return "".join(f"\\{ch}" if ch in _MDV2_SPECIAL_CHARS else ch for ch in text)


def to_telegram_markdown_v2(text: str) -> str:
    """Convert the **bold** markdown Gemini writes (CommonMark style,
    double asterisk) into Telegram's own MarkdownV2 syntax (single
    asterisk), escaping everything else so sending with
    parse_mode=MarkdownV2 doesn't get rejected over stray punctuation
    (a plain '.', '!', '-', etc. anywhere in the text is enough to
    fail entity parsing otherwise) (CIN-102).
    """
    parts = _BOLD_PATTERN.split(text)
    # re.split with one capturing group alternates [plain, bold, plain, bold, ..., plain]
    return "".join(
        f"*{_escape_markdown_v2(part)}*" if i % 2 else _escape_markdown_v2(part)
        for i, part in enumerate(parts)
    )


def strip_markdown(text: str) -> str:
    """Remove markdown emphasis markers for platforms whose captions
    render as plain text (Instagram, Facebook) -- otherwise the
    literal **/*/_/` characters show up unrendered in the published
    post (CIN-102).
    """
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"`(.+?)`", r"\1", text, flags=re.DOTALL)
    return text
