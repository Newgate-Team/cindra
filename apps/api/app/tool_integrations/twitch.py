"""Twitch for live streams: the streamer's own account, connected with
OAuth so the stream agent can read the channel's chat over EventSub
(Twitch's documented chat API) and post the avatar's replies.

Scopes: user:read:chat (EventSub channel.chat.* as this user) and
user:write:chat (Helix Send Chat Message). The refresh token is stored;
access tokens (about 4 hours) are minted on demand -- Twitch may rotate
the refresh token on every refresh, so the caller saves the new one.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings

SCOPES = ("user:read:chat", "user:write:chat")
_RETRYABLE = {429, 500, 502, 503, 504}


class TwitchError(Exception):
    pass


class TwitchAuthError(TwitchError):
    """The grant is revoked or expired -- the user must reconnect."""


class TwitchTransientError(TwitchError):
    pass


def _hosts() -> tuple[str, str]:
    base = get_settings().twitch_base_url.rstrip("/")
    if base:
        return base, base
    return "https://id.twitch.tv", "https://api.twitch.tv"


def _send(method: str, url: str, client: httpx.Client | None, **kwargs: Any) -> httpx.Response:
    request = client.request if client is not None else httpx.request
    try:
        return request(method, url, timeout=20.0, **kwargs)
    except httpx.TransportError as exc:
        raise TwitchTransientError(f"Twitch недоступен: {exc}") from exc


def _error(response: httpx.Response) -> TwitchError:
    try:
        message = response.json().get("message") or response.text[:200]
    except (ValueError, AttributeError):
        message = response.text[:200]
    if response.status_code in _RETRYABLE:
        return TwitchTransientError(f"Twitch {response.status_code}: {message}")
    if response.status_code in (400, 401, 403) and "token" in str(message).lower():
        return TwitchAuthError(f"Twitch отозвал доступ: {message}")
    if response.status_code == 401:
        return TwitchAuthError(f"Twitch: {message}")
    return TwitchError(f"Twitch {response.status_code}: {message}")


def authorization_url(state: str) -> str:
    settings = get_settings()
    auth, _ = _hosts()
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.twitch_client_id,
            "redirect_uri": settings.twitch_redirect_uri,
            "scope": " ".join(SCOPES),
            "state": state,
            # Always show Twitch's screen: a user switching accounts must
            # be able to pick another one rather than be signed in silently.
            "force_verify": "true",
        }
    )
    return f"{auth}/oauth2/authorize?{query}"


def _token(data: dict[str, str], client: httpx.Client | None) -> dict[str, Any]:
    settings = get_settings()
    auth, _ = _hosts()
    response = _send(
        "POST",
        f"{auth}/oauth2/token",
        client,
        data={
            **data,
            "client_id": settings.twitch_client_id,
            "client_secret": settings.twitch_client_secret,
        },
    )
    if response.status_code != 200:
        raise _error(response)
    return response.json()


def exchange_code(code: str, client: httpx.Client | None = None) -> dict[str, Any]:
    return _token(
        {
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": get_settings().twitch_redirect_uri,
        },
        client,
    )


def refresh(refresh_token: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """{"access_token", "refresh_token" (possibly a new one), ...}."""
    return _token({"grant_type": "refresh_token", "refresh_token": refresh_token}, client)


def validate(access_token: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """{"client_id", "login", "user_id", "scopes", "expires_in"}."""
    auth, _ = _hosts()
    response = _send(
        "GET", f"{auth}/oauth2/validate", client, headers={"Authorization": f"OAuth {access_token}"}
    )
    if response.status_code != 200:
        raise _error(response)
    return response.json()


def revoke(token: str, client: httpx.Client | None = None) -> None:
    auth, _ = _hosts()
    response = _send(
        "POST",
        f"{auth}/oauth2/revoke",
        client,
        data={"client_id": get_settings().twitch_client_id, "token": token},
    )
    if response.status_code != 200:
        raise _error(response)


def get_user(access_token: str, login: str, client: httpx.Client | None = None) -> dict | None:
    """A channel by login (the stream may read any channel's chat)."""
    _, helix = _hosts()
    response = _send(
        "GET",
        f"{helix}/helix/users",
        client,
        params={"login": login},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Client-Id": get_settings().twitch_client_id,
        },
    )
    if response.status_code != 200:
        raise _error(response)
    users = response.json().get("data") or []
    return users[0] if users else None
