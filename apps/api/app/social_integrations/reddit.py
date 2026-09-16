"""Reddit OAuth + posting.

Unlike every other platform adapter in this codebase, publish() never
downloads media itself. Reddit's native "asset upload" flow (a lease
request, an S3 multipart PUT, then a poll for processing) is a
genuinely separate, larger protocol from what's implemented here --
deliberately skipped for both images AND video, not just video like
linkedin.py. Instead, an image/video post uses Reddit's `kind=link`
post type with `url` pointing straight at Cindra's own public R2 URL:
this is a first-class Reddit post type (not a workaround), and Reddit
auto-embeds a direct image/video URL for display the same way a
native upload would. See publish_matrix.py's comment on this platform
for the same reasoning.

Also scoped deliberately: there is no subreddit-selection UI anywhere
in this app (Post/SocialAccount carry no such field), so every post
goes to the connected member's own profile ("u_{username}", Reddit's
pseudo-subreddit for personal posts) rather than a chosen community.

Built to Reddit's documented OAuth2 + API shape, but -- same honesty
caveat as linkedin.py -- this integration has not been exercised
against a real Reddit app (no partner credentials available yet).
Two things specifically worth double-checking before relying on this
beyond the mocked test coverage: (1) the token endpoint requires HTTP
Basic Auth with client_id:client_secret, unlike every other OAuth
integration here which sends the secret as a form field; (2) POST
/api/submit returns HTTP 200 even on failure -- the actual error, if
any, is embedded in the JSON body's `json.errors` array, not the
status code.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import get_settings
from app.models import Post, SocialAccount
from app.social_accounts import get_access_token, get_refresh_token
from app.social_integrations.errors import PermanentPublishError, TransientPublishError
from app.social_integrations.media_validation import validate_own_media_url
from app.token_crypto import encrypt_token

_AUTH_BASE = "https://www.reddit.com/api/v1"
_TOKEN_URL = f"{_AUTH_BASE}/access_token"
_API_BASE = "https://oauth.reddit.com"
_ME_URL = f"{_API_BASE}/api/v1/me"
_SUBMIT_URL = f"{_API_BASE}/api/submit"
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_TOKEN_REFRESH_MARGIN = timedelta(minutes=5)
_MAX_TITLE_LENGTH = 300


def _handle_json(response: httpx.Response) -> dict[str, Any]:
    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientPublishError(f"Reddit API {response.status_code}: {response.text[:500]}")
    try:
        body = response.json()
    except ValueError as exc:
        raise PermanentPublishError(
            f"Reddit API вернул не-JSON ответ ({response.status_code})"
        ) from exc

    if response.status_code >= 400:
        message = body.get("message") if isinstance(body, dict) else body
        raise PermanentPublishError(f"Reddit API {response.status_code}: {message or body}")
    return body


def exchange_code_for_token(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    user_agent: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    request = client.post if client is not None else httpx.post
    response = request(
        _TOKEN_URL,
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        auth=(client_id, client_secret),
        headers={"User-Agent": user_agent, "Content-Type": "application/x-www-form-urlencoded"},
        timeout=20.0,
    )
    return _handle_json(response)


def refresh_access_token(
    refresh_token: str,
    client_id: str,
    client_secret: str,
    user_agent: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    request = client.post if client is not None else httpx.post
    response = request(
        _TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        auth=(client_id, client_secret),
        headers={"User-Agent": user_agent, "Content-Type": "application/x-www-form-urlencoded"},
        timeout=20.0,
    )
    return _handle_json(response)


def get_identity(
    access_token: str, user_agent: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    """`name` is the member's own Reddit username -- stored as this
    app's external_account_id AND used to build the `sr=u_{name}`
    target at publish time (see this module's own docstring)."""
    request = client.get if client is not None else httpx.get
    response = request(
        _ME_URL,
        headers={"Authorization": f"Bearer {access_token}", "User-Agent": user_agent},
        timeout=20.0,
    )
    return _handle_json(response)


def ensure_fresh_access_token(
    account: SocialAccount, client: httpx.Client | None = None
) -> str:
    """Same refresh-on-demand contract as youtube.py's own
    ensure_fresh_access_token. Reddit only issues a refresh token when
    the authorization request carries `duration=permanent` (this
    app's /reddit/start always sends it -- see that endpoint), so a
    missing refresh token here means something went wrong with the
    original connection, not an expected per-app limitation like
    LinkedIn's."""
    expires_at = account.token_expires_at
    if expires_at is None or expires_at > datetime.now(UTC) + _TOKEN_REFRESH_MARGIN:
        return get_access_token(account)

    current_refresh_token = get_refresh_token(account)
    if not current_refresh_token:
        raise PermanentPublishError(
            "Reddit refresh token отсутствует — подключите аккаунт заново"
        )

    settings = get_settings()
    token = refresh_access_token(
        current_refresh_token,
        settings.reddit_client_id,
        settings.reddit_client_secret,
        settings.reddit_user_agent,
        client,
    )
    account.encrypted_access_token = encrypt_token(token["access_token"])
    # Reddit's refresh response doesn't repeat the refresh_token --
    # same as tiktok.py/youtube.py, keep the existing one.
    account.encrypted_refresh_token = encrypt_token(
        token.get("refresh_token") or current_refresh_token
    )
    account.token_expires_at = datetime.now(UTC) + timedelta(seconds=int(token["expires_in"]))
    return token["access_token"]


def publish(
    account: SocialAccount, post: Post, client: httpx.Client | None = None
) -> dict[str, Any]:
    if not post.text and not post.image_url and not post.video_url:
        raise PermanentPublishError("Публикация в Reddit требует текста или медиа")

    owns_client = client is None
    active_client = client or httpx.Client()
    try:
        access_token = ensure_fresh_access_token(account, active_client)
        settings = get_settings()
        title = (post.text or "Cindra post").strip()[:_MAX_TITLE_LENGTH] or "Cindra post"

        media_url = post.image_url or post.video_url
        form: dict[str, str] = {
            "api_type": "json",
            "sr": f"u_{account.external_account_id}",
            "title": title,
        }
        if media_url:
            validate_own_media_url(media_url, "Reddit")
            form["kind"] = "link"
            form["url"] = media_url
            # Without this, resubmitting the same generated URL a
            # second time (e.g. a retried publish) would fail with
            # Reddit's "ALREADY_SUB" error instead of just posting.
            form["resubmit"] = "true"
        else:
            form["kind"] = "self"
            form["text"] = post.text

        response = active_client.post(
            _SUBMIT_URL,
            data=form,
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": settings.reddit_user_agent,
            },
            timeout=30.0,
        )
        body = _handle_json(response)
        # Reddit's submit endpoint returns HTTP 200 even when the post
        # was rejected -- the actual error lives in this array, not
        # the status code. Covered by a dedicated negative-control
        # test (see test_reddit.py) since it's easy to miss.
        errors = body.get("json", {}).get("errors", [])
        if errors:
            raise PermanentPublishError(f"Reddit API отклонил пост: {errors}")
        data = body.get("json", {}).get("data", {})
        post_id = data.get("name") or data.get("id") or ""
        return {"id": post_id}
    finally:
        if owns_client:
            active_client.close()
