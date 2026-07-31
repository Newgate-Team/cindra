from app.content_pipeline.prompts import build_text_prompt
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
