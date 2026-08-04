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


def exchange_code_for_token(
    code: str,
    redirect_uri: str,
    app_id: str,
    app_secret: str,
    client: httpx.Client | None = None,
) -> str:
    """OAuth step 1: authorization code -> short-lived user access token."""
    params = {
        "client_id": app_id,
        "client_secret": app_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    url = f"{_GRAPH_API_BASE}/oauth/access_token"
    response = (
        client.get(url, params=params, timeout=15.0)
        if client is not None
        else httpx.get(url, params=params, timeout=15.0)
    )
    return _handle_response(response)["access_token"]


def get_long_lived_token(
    short_lived_token: str, app_id: str, app_secret: str, client: httpx.Client | None = None
) -> str:
    """OAuth step 2: short-lived token -> ~60-day long-lived token."""
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_lived_token,
    }
    url = f"{_GRAPH_API_BASE}/oauth/access_token"
    response = (
        client.get(url, params=params, timeout=15.0)
        if client is not None
        else httpx.get(url, params=params, timeout=15.0)
    )
    return _handle_response(response)["access_token"]


def discover_connected_accounts(
    access_token: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Find the Facebook Page (and the Instagram Business Account linked
    to it). Real 2-step Graph API lookup: list Pages the token can
    manage, then read each Page's linked IG account -- the first Page
    found with one linked wins for both (see CIN-65: Cindra publishes
    to whichever Page the user picked when linking Instagram, so the
    Facebook Page account we create has to be *that* Page, not just
    any Page the user happens to manage).

    `page["access_token"]` here is the Page Access Token (not the user
    token) -- /me/accounts returns it by default alongside id/name, no
    extra request needed. It's long-lived as long as the user token
    used to fetch it was already exchanged for a long-lived one (see
    get_long_lived_token, called before this in the connect flow).

    Raises PermanentPublishError if no Page has an IG account linked
    (the account's IG profile isn't converted to Business/Creator, or
    isn't linked to any Facebook Page -- Meta requires both).
    """
    pages_url = f"{_GRAPH_API_BASE}/me/accounts"
    pages_response = (
        client.get(pages_url, params={"access_token": access_token}, timeout=15.0)
        if client is not None
        else httpx.get(pages_url, params={"access_token": access_token}, timeout=15.0)
    )
    pages = _handle_response(pages_response).get("data", [])

    for page in pages:
        page_url = f"{_GRAPH_API_BASE}/{page['id']}"
        params = {"fields": "instagram_business_account{id,username}", "access_token": access_token}
        page_detail_response = (
            client.get(page_url, params=params, timeout=15.0)
            if client is not None
            else httpx.get(page_url, params=params, timeout=15.0)
        )
        page_detail = _handle_response(page_detail_response)
        ig_account = page_detail.get("instagram_business_account")
        if ig_account:
            return {
                "instagram": ig_account,
                "facebook_page": {
                    "id": page["id"],
                    "name": page.get("name"),
                    "access_token": page["access_token"],
                },
            }

    raise PermanentPublishError(
        "Ни одна из Facebook-страниц не привязана к Instagram Business/Creator аккаунту"
    )


def create_media_container(
    ig_user_id: str,
    image_url: str,
    caption: str,
    access_token: str,
    media_type: str | None = None,
    client: httpx.Client | None = None,
) -> str:
    """`media_type="STORIES"` publishes a Story instead of a regular feed
    post (CIN-74). Stories don't carry a caption in the Content
    Publishing API -- `caption` is only sent for regular posts."""
    url = f"{_GRAPH_API_BASE}/{ig_user_id}/media"
    params: dict[str, str] = {"image_url": image_url, "access_token": access_token}
    if media_type == "STORIES":
        params["media_type"] = media_type
    else:
        params["caption"] = caption
    response = (
        client.post(url, params=params, timeout=30.0)
        if client is not None
        else httpx.post(url, params=params, timeout=30.0)
    )
    return _handle_response(response)["id"]


def publish_media(
    ig_user_id: str, creation_id: str, access_token: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    url = f"{_GRAPH_API_BASE}/{ig_user_id}/media_publish"
    params = {"creation_id": creation_id, "access_token": access_token}
    response = (
        client.post(url, params=params, timeout=15.0)
        if client is not None
        else httpx.post(url, params=params, timeout=15.0)
    )
    return _handle_response(response)


def publish(account: SocialAccount, post: Post) -> dict[str, Any]:
    """Registered in app.scheduler.registry as the instagram publisher.

    Unlike Telegram, Instagram's Content Publishing API has no
    text-only post -- every post needs media, so this is a permanent
    (not transient) failure when image_url is missing rather than
    something retrying would fix.
    """
    if not post.image_url:
        raise PermanentPublishError(
            "Instagram требует изображение: у Post нет image_url "
            "(текстовые посты не поддерживаются Content Publishing API)"
        )
    access_token = get_access_token(account)
    media_type = "STORIES" if post.content_kind == "story" else None
    creation_id = create_media_container(
        account.external_account_id, post.image_url, post.text, access_token, media_type
    )
    return publish_media(account.external_account_id, creation_id, access_token)
