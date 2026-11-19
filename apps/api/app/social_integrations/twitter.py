"""X (Twitter) OAuth 2.0 + posting.

Text-only, deliberately -- this module never implements X's media
upload at all, not even scoped down the way linkedin.py drops video.
X's media upload lives on a separate legacy v1.1 endpoint
(upload.twitter.com/1.1/media/upload.json) with its own chunked
INIT/APPEND/FINALIZE/STATUS protocol, and unlike Reddit there is no
first-class "link post" equivalent that displays media inline without
it -- a URL placed in tweet text only ever renders as a link-preview
card, not an attached image/video. publish() raises a clear
PermanentPublishError for an image/video post rather than silently
mishandling it (same shape as publish_matrix.py's own comment on this
platform).

The only integration in this app that requires PKCE (Proof Key for
Code Exchange) -- X's OAuth2 user-context flow mandates it, unlike
TikTok/YouTube/LinkedIn/Reddit. See security.py's
create_twitter_oauth_state for how the code_verifier is carried
across the redirect round trip.

Same honesty caveat as linkedin.py/reddit.py: not yet exercised
against a real X Developer app. One thing specifically worth flagging
beyond the usual "double-check field names" caveat -- since 2023, X's
free API tier does not include write access to POST /2/tweets at all;
a paid tier is required. That's a real, additional gate beyond just
having TWITTER_CLIENT_ID/SECRET configured, unlike every other
platform in this codebase (a free developer app is enough for those).
Whoever configures real credentials should confirm their X API plan
actually includes tweet-write access before relying on this.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import get_settings
from app.models import Post, SocialAccount
from app.social_accounts import get_access_token, get_refresh_token
from app.social_integrations.errors import PermanentPublishError, TransientPublishError
from app.token_crypto import encrypt_token

_TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
_ME_URL = "https://api.twitter.com/2/users/me"
_TWEETS_URL = "https://api.twitter.com/2/tweets"
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_TOKEN_REFRESH_MARGIN = timedelta(minutes=5)
_MAX_TWEET_LENGTH = 280


def _handle_json(response: httpx.Response) -> dict[str, Any]:
    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientPublishError(f"X API {response.status_code}: {response.text[:500]}")
    try:
        body = response.json()
    except ValueError as exc:
        raise PermanentPublishError(f"X API вернул не-JSON ответ ({response.status_code})") from exc

    if response.status_code >= 400:
        message = body.get("detail") or body.get("title") if isinstance(body, dict) else body
        raise PermanentPublishError(f"X API {response.status_code}: {message or body}")
    return body


def exchange_code_for_token(
    code: str,
    code_verifier: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    request = client.post if client is not None else httpx.post
    response = request(
        _TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "client_id": client_id,
        },
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20.0,
    )
    return _handle_json(response)


def refresh_access_token(
    refresh_token: str,
    client_id: str,
    client_secret: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    request = client.post if client is not None else httpx.post
    response = request(
        _TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20.0,
    )
    return _handle_json(response)


def get_me(access_token: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """Returns the `data` object ({"id", "name", "username"}) -- `id`
    is stored as this app's external_account_id."""
    request = client.get if client is not None else httpx.get
    response = request(
        _ME_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20.0,
    )
    body = _handle_json(response)
    data = body.get("data")
    if not data:
        raise PermanentPublishError("X API не вернул данные пользователя")
    return data


def ensure_fresh_access_token(
    account: SocialAccount, client: httpx.Client | None = None
) -> str:
    """Same refresh-on-demand contract as reddit.py/youtube.py's own
    ensure_fresh_access_token. A refresh token requires the
    offline.access scope (always requested by this app's
    /twitter/start), so a missing one here means something went wrong
    with the original connection."""
    expires_at = account.token_expires_at
    if expires_at is None or expires_at > datetime.now(UTC) + _TOKEN_REFRESH_MARGIN:
        return get_access_token(account)

    current_refresh_token = get_refresh_token(account)
    if not current_refresh_token:
        raise PermanentPublishError("X refresh token отсутствует — подключите аккаунт заново")

    settings = get_settings()
    token = refresh_access_token(
        current_refresh_token, settings.twitter_client_id, settings.twitter_client_secret, client
    )
    account.encrypted_access_token = encrypt_token(token["access_token"])
    # X rotates refresh tokens on every use and DOES always return a
    # new one -- unlike tiktok.py/youtube.py's "keep the old one if
    # absent" fallback, but that fallback is harmless here too since
    # it only ever triggers if the API response is missing the field.
    account.encrypted_refresh_token = encrypt_token(
        token.get("refresh_token") or current_refresh_token
    )
    account.token_expires_at = datetime.now(UTC) + timedelta(seconds=int(token["expires_in"]))
    return token["access_token"]


def publish(
    account: SocialAccount, post: Post, client: httpx.Client | None = None
) -> dict[str, Any]:
    if post.image_url or post.video_url:
        raise PermanentPublishError("Публикация изображений и видео в X пока не поддерживается")
    if not post.text:
        raise PermanentPublishError("Публикация в X требует текста")

    owns_client = client is None
    active_client = client or httpx.Client()
    try:
        access_token = ensure_fresh_access_token(account, active_client)
        response = active_client.post(
            _TWEETS_URL,
            json={"text": post.text[:_MAX_TWEET_LENGTH]},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        body = _handle_json(response)
        data = body.get("data", {})
        return {"id": data.get("id", "")}
    finally:
        if owns_client:
            active_client.close()
