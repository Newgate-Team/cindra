"""HeyGen LiveAvatar (api.liveavatar.com): real-time streaming avatars,
used to host live streams. A separate product from the HeyGen video
API: its own key (app.liveavatar.com -> Developers) and its own credits
-- a HeyGen API key does not work here.

The API side only checks the key, shows credits and lists avatars; the
stream agent runs the actual sessions with the same key.

Every response is wrapped as {"code": 100, "data": ..., "message": ...}.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings

_RETRYABLE = {429, 500, 502, 503, 504}


class LiveAvatarError(Exception):
    pass


class LiveAvatarAuthError(LiveAvatarError):
    pass


class LiveAvatarTransientError(LiveAvatarError):
    pass


def base_url() -> str:
    return get_settings().liveavatar_api_base_url.rstrip("/")


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        detail = body.get("message") or body.get("detail")
        if isinstance(detail, list):  # FastAPI-style 422
            detail = "; ".join(str(item.get("msg", item)) for item in detail)
        if detail:
            return str(detail)
    return f"HTTP {response.status_code}"


def _get(api_key: str, path: str, params: dict[str, Any] | None, client: httpx.Client | None):
    send = client.get if client is not None else httpx.get
    try:
        response = send(
            f"{base_url()}{path}",
            params=params,
            headers={"X-API-KEY": api_key, "Accept": "application/json"},
            timeout=20.0,
        )
    except httpx.TransportError as exc:
        raise LiveAvatarTransientError(f"LiveAvatar недоступен: {exc}") from exc
    if response.status_code in (401, 403):
        raise LiveAvatarAuthError(_message(response))
    if response.status_code in _RETRYABLE:
        raise LiveAvatarTransientError(f"LiveAvatar {response.status_code}: {_message(response)}")
    if response.status_code >= 400:
        raise LiveAvatarError(f"LiveAvatar {response.status_code}: {_message(response)}")
    body = response.json()
    if not isinstance(body, dict) or body.get("data") is None:
        raise LiveAvatarError(f"LiveAvatar: {body.get('message') if isinstance(body, dict) else body}")
    return body["data"]


def get_credits(api_key: str, client: httpx.Client | None = None) -> float | None:
    """Credits left on the account -- also the cheapest way to check a
    key is valid. credits_left is a decimal *string* in their schema."""
    data = _get(api_key, "/v1/users/credits", None, client)
    try:
        return float(data["credits_left"])
    except (KeyError, TypeError, ValueError):
        return None


def list_avatars(
    api_key: str,
    *,
    public: bool,
    page: int = 1,
    page_size: int = 100,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """{"items": [...], "next_page": int | None}. Own avatars still in
    training (status != ACTIVE) are returned flagged, not hidden -- the
    user should see why their avatar isn't selectable yet."""
    data = _get(
        api_key,
        "/v1/avatars/public" if public else "/v1/avatars",
        {"page": page, "page_size": page_size},
        client,
    )
    items = []
    for avatar in data.get("results", []):
        voice = avatar.get("default_voice") or {}
        items.append(
            {
                "id": avatar["id"],
                "name": avatar.get("name") or "Без названия",
                "preview_url": avatar.get("preview_url"),
                "status": avatar.get("status", "INIT"),
                "type": avatar.get("type", "VIDEO"),
                "default_voice_name": voice.get("name"),
                "ready": avatar.get("status") == "ACTIVE" and not avatar.get("is_expired"),
            }
        )
    return {"items": items, "next_page": page + 1 if data.get("next") else None}
