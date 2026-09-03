from urllib.parse import urlparse

from app.config import get_settings
from app.social_integrations.errors import PermanentPublishError


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
    allowed_base = get_settings().r2_public_url_base.rstrip("/")
    parsed = urlparse(url)
    if not allowed_base or parsed.scheme != "https" or not url.startswith(f"{allowed_base}/"):
        raise PermanentPublishError(
            f"{platform} может загрузить медиа только из настроенного публичного "
            "Cindra media bucket"
        )
