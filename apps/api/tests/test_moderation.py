import pytest

from app.content_pipeline.errors import ContentModeratedError
from app.content_pipeline.moderation import check_content


def test_clean_text_passes() -> None:
    check_content("Отличный кофе по утрам заряжает энергией на весь день.")


def test_profanity_is_flagged() -> None:
    with pytest.raises(ContentModeratedError):
        check_content("это просто пиздатый кофе")


def test_profanity_check_is_case_insensitive() -> None:
    with pytest.raises(ContentModeratedError):
        check_content("ЭТО СУКА ХОРОШИЙ КОФЕ")


def test_custom_blocked_term_is_flagged() -> None:
    with pytest.raises(ContentModeratedError):
        check_content("Лучше, чем у Конкурента", blocked_terms=frozenset({"конкурента"}))


def test_custom_blocked_term_absent_passes() -> None:
    check_content("Обычный пост про кофе", blocked_terms=frozenset({"конкурента"}))
