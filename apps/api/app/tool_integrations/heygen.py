"""HeyGen API v3 client, called with the *user's own* API key.

Endpoints and response shapes checked against developers.heygen.com
(v3 reference: /v3/users/me, /v3/avatars/looks, /v3/voices) on
2026-10-10. HeyGen also offers OAuth for third-party apps, but only
after an approved integration-intake application -- a bring-your-own
API key works for every customer today, so that's what this starts
with; OAuth can be added beside it once HeyGen approves Cindra.

The key travels in the X-Api-Key header, never the URL, so an error
message built from the response can't leak it (the Gemini ?key=
problem from CIN-111 doesn't apply here).
"""

from __future__ import annotations

from typing import Any

import httpx

_BASE_URL = "https://api.heygen.com"
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class HeyGenError(Exception):
    """A request HeyGen rejected for a reason retrying won't fix."""


class HeyGenAuthError(HeyGenError):
    """The key is invalid, expired, or lacks permission for the call."""


class HeyGenTransientError(HeyGenError):
    """Rate limit, 5xx, or a network failure -- worth retrying."""


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or error)
    if isinstance(body, dict) and body.get("message"):
        return str(body["message"])
    return response.text[:300]


def _request(
    method: str,
    path: str,
    api_key: str,
    client: httpx.Client | None = None,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    send = client.request if client is not None else httpx.request
    try:
        response = send(
            method,
            f"{_BASE_URL}{path}",
            headers={"X-Api-Key": api_key},
            params={k: v for k, v in (params or {}).items() if v is not None},
            json=json,
            timeout=30.0,
        )
    except httpx.TransportError as exc:
        raise HeyGenTransientError(f"HeyGen недоступен: {exc}") from exc

    if response.status_code in (401, 403):
        raise HeyGenAuthError(_error_message(response))
    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise HeyGenTransientError(f"HeyGen {response.status_code}: {_error_message(response)}")
    if response.status_code >= 400:
        raise HeyGenError(f"HeyGen {response.status_code}: {_error_message(response)}")
    try:
        body = response.json()
    except ValueError as exc:
        raise HeyGenError(f"HeyGen вернул не-JSON ответ ({response.status_code})") from exc
    if not isinstance(body, dict):
        raise HeyGenError("HeyGen вернул ответ неожиданного формата")
    return body


def get_current_user(api_key: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """Doubles as key validation: HeyGen's own docs name this call as
    the way to verify a key."""
    return _request("GET", "/v3/users/me", api_key, client).get("data") or {}


def account_label(user: dict[str, Any]) -> str | None:
    return user.get("email") or user.get("username")


def wallet_balance(user: dict[str, Any]) -> tuple[float | None, str | None]:
    wallet = user.get("wallet")
    if not isinstance(wallet, dict):
        return None, None
    return wallet.get("remaining_balance"), wallet.get("currency")


def list_avatar_looks(
    api_key: str,
    token: str | None = None,
    ownership: str | None = None,
    limit: int = 50,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """A "look" is HeyGen's unit you actually render with -- its `id` is
    what POST /v3/videos takes as `avatar_id`, not the avatar group id."""
    body = _request(
        "GET",
        "/v3/avatars/looks",
        api_key,
        client,
        params={"token": token, "ownership": ownership, "limit": limit},
    )
    items = [
        {
            "id": look["id"],
            "name": look.get("name") or look["id"],
            "avatar_type": look.get("avatar_type"),
            "gender": look.get("gender"),
            "preview_image_url": look.get("preview_image_url"),
            "preview_video_url": look.get("preview_video_url"),
            "default_voice_id": look.get("default_voice_id"),
        }
        for look in body.get("data") or []
        if isinstance(look, dict) and look.get("id")
    ]
    return {"items": items, "next_token": body.get("next_token") if body.get("has_more") else None}


def list_voices(
    api_key: str,
    token: str | None = None,
    language: str | None = None,
    gender: str | None = None,
    limit: int = 100,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    body = _request(
        "GET",
        "/v3/voices",
        api_key,
        client,
        params={"token": token, "language": language, "gender": gender, "limit": limit},
    )
    items = [
        {
            "voice_id": voice["voice_id"],
            "name": voice.get("name") or voice["voice_id"],
            "language": voice.get("language"),
            "gender": voice.get("gender"),
            "preview_audio_url": voice.get("preview_audio_url"),
        }
        for voice in body.get("data") or []
        if isinstance(voice, dict) and voice.get("voice_id")
    ]
    return {"items": items, "next_token": body.get("next_token") if body.get("has_more") else None}
