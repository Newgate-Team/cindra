from app.models import GenerationContentType, SocialPlatform

_TEXT = GenerationContentType.text
_IMAGE = GenerationContentType.image
_VIDEO = GenerationContentType.video

# What each platform can actually publish -- Instagram's Content
# Publishing API has no text-only post (see instagram.py's publish()
# guard), so it's excluded here rather than only failing later at
# publish time.
ALLOWED_CONTENT_TYPES: dict[SocialPlatform, frozenset[GenerationContentType]] = {
    SocialPlatform.telegram: frozenset({_TEXT, _IMAGE, _VIDEO}),
    SocialPlatform.facebook: frozenset({_TEXT, _IMAGE, _VIDEO}),
    SocialPlatform.instagram: frozenset({_IMAGE, _VIDEO}),
    SocialPlatform.tiktok: frozenset({_VIDEO}),
    SocialPlatform.youtube: frozenset({_VIDEO}),
    # Video deliberately excluded for now -- linkedin.py's publish()
    # only implements text + image, LinkedIn's multi-part video
    # finalize flow is a scoped-out follow-up (see that module's
    # docstring). Not declaring it here means a generation targeting
    # LinkedIn is rejected up front (CIN-style "check before spending
    # budget") instead of generating a video that then fails at the
    # very last step.
    SocialPlatform.linkedin: frozenset({_TEXT, _IMAGE}),
    # Unlike LinkedIn's video exclusion, this isn't a scoped-out corner
    # -- reddit.py deliberately never implements Reddit's native
    # asset-upload lease/S3 protocol at all, for text OR media. Every
    # Cindra-generated image/video already has a stable public R2 URL,
    # and Reddit's "link" post kind (a first-class post type, not a
    # workaround) auto-embeds a direct image/video URL the same way a
    # native upload would display. So image and video are both fully
    # supported here, just via `kind=link` instead of asset upload.
    SocialPlatform.reddit: frozenset({_TEXT, _IMAGE, _VIDEO}),
}

# content_kind per platform+content_type. "story" only exists as a
# concept for Instagram (see instagram.py's media_type="STORIES");
# "video_script" is a text content_kind (a script, not a video itself).
ALLOWED_CONTENT_KINDS: dict[SocialPlatform, dict[GenerationContentType, frozenset[str]]] = {
    SocialPlatform.telegram: {
        _TEXT: frozenset({"post", "video_script"}),
        _IMAGE: frozenset({"post"}),
        _VIDEO: frozenset({"post"}),
    },
    SocialPlatform.facebook: {
        _TEXT: frozenset({"post", "video_script"}),
        _IMAGE: frozenset({"post"}),
        _VIDEO: frozenset({"post"}),
    },
    SocialPlatform.instagram: {
        _IMAGE: frozenset({"post", "story"}),
        _VIDEO: frozenset({"post", "story"}),
    },
    SocialPlatform.tiktok: {
        _VIDEO: frozenset({"post"}),
    },
    SocialPlatform.youtube: {
        _VIDEO: frozenset({"post"}),
    },
    SocialPlatform.linkedin: {
        _TEXT: frozenset({"post", "video_script"}),
        _IMAGE: frozenset({"post"}),
    },
    SocialPlatform.reddit: {
        _TEXT: frozenset({"post", "video_script"}),
        _IMAGE: frozenset({"post"}),
        _VIDEO: frozenset({"post"}),
    },
}


class InvalidGenerationTargetError(Exception):
    """Raised when the requested content_type/content_kind can't be
    published to (the intersection of) all the selected target
    platforms."""


def allowed_content_types_for(platforms: set[SocialPlatform]) -> set[GenerationContentType]:
    """Content types publishable to every one of `platforms` at once."""
    if not platforms:
        return set()
    result = set(ALLOWED_CONTENT_TYPES[next(iter(platforms))])
    for platform in platforms:
        result &= ALLOWED_CONTENT_TYPES[platform]
    return result


def allowed_content_kinds_for(
    platforms: set[SocialPlatform], content_type: GenerationContentType
) -> set[str]:
    """content_kind values valid for `content_type` on every one of
    `platforms` at once. Empty if any platform can't publish
    `content_type` at all."""
    if not platforms:
        return set()
    result: set[str] | None = None
    for platform in platforms:
        kinds = ALLOWED_CONTENT_KINDS[platform].get(content_type, frozenset())
        result = set(kinds) if result is None else (result & kinds)
    return result or set()


def validate_generation_target(
    platforms: set[SocialPlatform], content_type: GenerationContentType, content_kind: str
) -> None:
    allowed_types = allowed_content_types_for(platforms)
    if content_type not in allowed_types:
        offending = sorted(
            p.value for p in platforms if content_type not in ALLOWED_CONTENT_TYPES[p]
        )
        raise InvalidGenerationTargetError(
            f"Формат «{content_type.value}» не поддерживается для: {', '.join(offending)}"
        )

    allowed_kinds = allowed_content_kinds_for(platforms, content_type)
    if content_kind not in allowed_kinds:
        offending = sorted(
            p.value
            for p in platforms
            if content_kind not in ALLOWED_CONTENT_KINDS[p].get(content_type, frozenset())
        )
        raise InvalidGenerationTargetError(
            f"Тип контента «{content_kind}» не поддерживается для: {', '.join(offending)} "
            f"при формате «{content_type.value}»"
        )
