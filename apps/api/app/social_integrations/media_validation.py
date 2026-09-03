from urllib.parse import urlparse

from app.config import get_settings
from app.social_integrations.errors import PermanentPublishError


def is_own_media_url(url: str) -> bool:
    """True iff `url` is under Cindra's own configured public R2 bucket
    (CIN-134, generalized in CIN-156, reused across packages in CIN-161).

    Any code path that downloads a client-supplied URL itself (rather
    than handing the URL to a third party and letting it fetch) MUST
    check this first -- without it, a server-side download turns the
    worker into an SSRF proxy that will fetch any http(s) URL,
    including internal/private network addresses, on the caller's
    behalf. Pure predicate (no raising) so each caller can pick the
    exception type that fits its own domain -- see
    validate_own_media_url below for the social_integrations callers,
    and content_pipeline/attachments.py::fetch_attachment_bytes for a
    caller that raises its own UnsupportedAttachmentError instead.
    """
    allowed_base = get_settings().r2_public_url_base.rstrip("/")
    parsed = urlparse(url)
    return bool(allowed_base) and parsed.scheme == "https" and url.startswith(f"{allowed_base}/")


def validate_own_media_url(url: str, platform: str) -> None:
    """Only allow fetching media from Cindra's own configured public R2
    bucket (CIN-134, generalized in CIN-156).

    Any integration that downloads a Post.image_url/video_url itself
    (rather than handing the URL to the platform's API and letting the
    platform fetch it) MUST call this first. Post.image_url/video_url
    are plain, unvalidated strings on PostCreate -- any authenticated
    user can call POST /posts directly with an arbitrary URL, so
    without this check a server-side download turns the worker into an
    SSRF proxy that will fetch any http(s) URL, including
    internal/private network addresses, on the caller's behalf.

    First applied to TikTok (Content Posting API's FILE_UPLOAD requires
    us to hold the bytes); Telegram's send_video needs the identical
    guard for the identical reason (CIN-115's direct-upload workaround
    for Telegram's 20MB URL-fetch cap) but didn't get it until CIN-156
    caught the gap.
    """
    if not is_own_media_url(url):
        raise PermanentPublishError(
            f"{platform} может загрузить медиа только из настроенного публичного "
            "Cindra media bucket"
        )
