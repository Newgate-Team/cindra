import re

from app.content_pipeline.errors import ContentModeratedError

# Heuristic keyword blocklist -- a real moderation API (OpenAI
# Moderation, Claude classification, etc.) is future work; no such
# provider is decided yet (same open question as CIN-50 for images/
# video). This still meaningfully protects against the two most
# common failure modes for auto-published content: profanity slipping
# into a public post, and mentioning a banned/competitor term the
# brand explicitly doesn't want associated with it.
_PROFANITY = {"бляд", "хуй", "пизд", "ебан", "сука"}


def check_content(text: str, blocked_terms: frozenset[str] = frozenset()) -> None:
    """Raise ContentModeratedError if `text` contains blocked language.

    `blocked_terms` lets a caller pass brand-specific terms (e.g. a
    competitor name) on top of the built-in profanity list.
    """
    lowered = text.lower()

    for term in _PROFANITY:
        if re.search(re.escape(term), lowered):
            raise ContentModeratedError(f"Обнаружена нецензурная лексика: «{term}»")

    for term in blocked_terms:
        if term.lower() in lowered:
            raise ContentModeratedError(f"Обнаружен запрещённый термин: «{term}»")
