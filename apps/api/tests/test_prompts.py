from app.content_pipeline.prompts import build_story_overlay_prompt, build_text_prompt
from app.models import SocialPlatform


def test_includes_topic_and_platform_guidance() -> None:
    prompt = build_text_prompt("осенняя коллекция", SocialPlatform.instagram)
    assert "осенняя коллекция" in prompt
    assert "хэштегов" in prompt


def test_telegram_guidance_differs_from_instagram() -> None:
    telegram_prompt = build_text_prompt("тема", SocialPlatform.telegram)
    instagram_prompt = build_text_prompt("тема", SocialPlatform.instagram)
    assert telegram_prompt != instagram_prompt


def test_brand_guide_included_when_present() -> None:
    prompt = build_text_prompt("тема", SocialPlatform.telegram, brand_guide="дружелюбно, без канцелярита")
    assert "дружелюбно, без канцелярита" in prompt


def test_brand_guide_omitted_when_absent() -> None:
    prompt = build_text_prompt("тема", SocialPlatform.telegram)
    assert "Бренд-гайд" not in prompt


def test_unknown_content_kind_falls_back_to_post() -> None:
    prompt = build_text_prompt("тема", SocialPlatform.telegram, content_kind="does-not-exist")
    assert "Обычный пост." in prompt


def test_video_script_guidance() -> None:
    prompt = build_text_prompt("тема", SocialPlatform.instagram, content_kind="video_script")
    assert "таймкодами" in prompt


def test_single_attachment_text_uses_generic_label() -> None:
    prompt = build_text_prompt(
        "тема", SocialPlatform.telegram, attachment_texts=["план запуска"]
    )
    assert "Контекст из прикреплённого документа: план запуска" in prompt


def test_multiple_attachment_texts_are_numbered() -> None:
    prompt = build_text_prompt(
        "тема", SocialPlatform.telegram, attachment_texts=["план запуска", "список продуктов"]
    )
    assert "Контекст из документа 1: план запуска" in prompt
    assert "Контекст из документа 2: список продуктов" in prompt


def test_facebook_has_its_own_guidance_and_does_not_raise() -> None:
    # Regression check (CIN-106): _PLATFORM_GUIDANCE previously had no
    # facebook entry and used a hard dict lookup, so this raised an
    # uncaught KeyError inside the Celery task.
    prompt = build_text_prompt("тема", SocialPlatform.facebook)
    assert "Facebook" in prompt


def test_story_overlay_prompt_asks_for_a_short_phrase() -> None:
    prompt = build_story_overlay_prompt("осенняя коллекция")
    assert "осенняя коллекция" in prompt
    assert "2-6 слов" in prompt


def test_story_overlay_prompt_includes_brand_guide_when_present() -> None:
    prompt = build_story_overlay_prompt("тема", brand_guide="дружелюбно, без канцелярита")
    assert "дружелюбно, без канцелярита" in prompt


def test_story_overlay_prompt_omits_brand_guide_when_absent() -> None:
    prompt = build_story_overlay_prompt("тема")
    assert "Бренд-гайд" not in prompt
