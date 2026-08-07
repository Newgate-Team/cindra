import pytest

from app.content_pipeline.publish_matrix import (
    InvalidGenerationTargetError,
    allowed_content_kinds_for,
    allowed_content_types_for,
    validate_generation_target,
)
from app.models import GenerationContentType, SocialPlatform


def test_instagram_alone_excludes_text() -> None:
    assert allowed_content_types_for({SocialPlatform.instagram}) == {
        GenerationContentType.image,
        GenerationContentType.video,
    }


def test_telegram_alone_allows_everything() -> None:
    assert allowed_content_types_for({SocialPlatform.telegram}) == {
        GenerationContentType.text,
        GenerationContentType.image,
        GenerationContentType.video,
    }


def test_mixed_targets_intersect_to_media_only() -> None:
    result = allowed_content_types_for({SocialPlatform.instagram, SocialPlatform.telegram})
    assert result == {GenerationContentType.image, GenerationContentType.video}


def test_story_only_allowed_for_instagram() -> None:
    kinds = allowed_content_kinds_for({SocialPlatform.instagram}, GenerationContentType.image)
    assert kinds == {"post", "story"}

    kinds = allowed_content_kinds_for({SocialPlatform.telegram}, GenerationContentType.image)
    assert kinds == {"post"}


def test_story_excluded_when_mixed_with_non_instagram_target() -> None:
    kinds = allowed_content_kinds_for(
        {SocialPlatform.instagram, SocialPlatform.telegram}, GenerationContentType.image
    )
    assert kinds == {"post"}


def test_validate_instagram_text_raises() -> None:
    with pytest.raises(InvalidGenerationTargetError, match="instagram"):
        validate_generation_target({SocialPlatform.instagram}, GenerationContentType.text, "post")


def test_validate_instagram_video_script_raises() -> None:
    # video_script is a text content_kind -- doesn't apply to
    # content_type=image at all, regardless of platform.
    with pytest.raises(InvalidGenerationTargetError):
        validate_generation_target({SocialPlatform.instagram}, GenerationContentType.image, "video_script")


def test_validate_valid_combo_does_not_raise() -> None:
    validate_generation_target({SocialPlatform.telegram}, GenerationContentType.text, "post")
    validate_generation_target({SocialPlatform.instagram}, GenerationContentType.image, "story")
    validate_generation_target(
        {SocialPlatform.instagram, SocialPlatform.facebook}, GenerationContentType.image, "post"
    )


def test_validate_facebook_text_does_not_raise() -> None:
    # Regression check for the sibling KeyError bug fixed alongside
    # this module (prompts.py's _PLATFORM_GUIDANCE previously had no
    # facebook entry) -- facebook is a fully valid text target.
    validate_generation_target({SocialPlatform.facebook}, GenerationContentType.text, "post")
