from typing import Any

import httpx

from app.models import Post, SocialAccount
from app.social_accounts import get_access_token
from app.social_integrations.errors import PermanentPublishError, TransientPublishError

_GRAPH_API_BASE = "https://graph.facebook.com/v21.0"

# 429/4 (rate limit) and 5xx are worth retrying; anything else (bad
# token, permission revoked, malformed request) is permanent.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _handle_response(response: httpx.Response) -> dict[str, Any]:
    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientPublishError(f"Graph API {response.status_code}: {response.text[:500]}")
    body = response.json()
    if "error" in body:
        raise PermanentPublishError(f"Graph API error: {body['error'].get('message', body)}")
    return body


def publish_text(
    page_id: str, message: str, access_token: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Text-only Page feed post -- POST /{page-id}/feed."""
    url = f"{_GRAPH_API_BASE}/{page_id}/feed"
    params = {"message": message, "access_token": access_token}
    response = (
        client.post(url, params=params, timeout=30.0)
        if client is not None
        else httpx.post(url, params=params, timeout=30.0)
    )
    return _handle_response(response)


def publish_photo(
    page_id: str,
    image_url: str,
    caption: str,
    access_token: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Photo Page post -- POST /{page-id}/photos, published by URL (Facebook
    fetches the image itself, no upload needed on our side)."""
    url = f"{_GRAPH_API_BASE}/{page_id}/photos"
    params = {"url": image_url, "caption": caption, "access_token": access_token}
    response = (
        client.post(url, params=params, timeout=30.0)
        if client is not None
        else httpx.post(url, params=params, timeout=30.0)
    )
    return _handle_response(response)


def publish_video(
    page_id: str,
    video_url: str,
    description: str,
    access_token: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Video Page post -- POST /{page-id}/videos, published by URL (`file_url`,
    Facebook fetches and processes the video itself, no upload needed on our
    side). Unlike /photos, this endpoint's caption field is `description`,
    not `caption`."""
    url = f"{_GRAPH_API_BASE}/{page_id}/videos"
    params = {"file_url": video_url, "description": description, "access_token": access_token}
    response = (
        client.post(url, params=params, timeout=30.0)
        if client is not None
        else httpx.post(url, params=params, timeout=30.0)
    )
    return _handle_response(response)


def publish(account: SocialAccount, post: Post) -> dict[str, Any]:
    """Registered in app.scheduler.registry as the facebook publisher.

    Like Telegram (and unlike Instagram's Content Publishing API), a
    Facebook Page feed post doesn't require media -- video_url/
    image_url just switch /feed for /videos/ /photos when present (a
    Post carries at most one of the two, see CIN-93). access_token
    here is the Page Access Token stored at connect time (see CIN-68),
    not a user token.
    """
    access_token = get_access_token(account)
    if post.video_url:
        return publish_video(account.external_account_id, post.video_url, post.text, access_token)
    if post.image_url:
        return publish_photo(account.external_account_id, post.image_url, post.text, access_token)
    return publish_text(account.external_account_id, post.text, access_token)
