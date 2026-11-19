"""HeyGen API v3 client, called with the *user's own* API key.

Every request and every field read here is checked against HeyGen's
own OpenAPI schemas by tests/test_heygen_contract.py (via
devtools/heygen_contract.py) -- not just against the prose docs.
Bring-your-own API key rather than HeyGen's OAuth: OAuth needs an
approved integration application, a key works for every customer.

The key travels in the X-Api-Key header, never the URL, so an error
message built from the response can't leak it (the Gemini ?key=
problem from CIN-111 doesn't apply here).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from app.config import get_settings

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def base_url() -> str:
    """https://api.heygen.com unless overridden -- the override exists
    so the whole flow can run against devtools/heygen_simulator.py."""
    return get_settings().heygen_api_base_url.rstrip("/")


class HeyGenError(Exception):
    """A request HeyGen rejected for a reason retrying won't fix."""


class HeyGenAuthError(HeyGenError):
    """The key is invalid, expired, or lacks permission for the call."""


class HeyGenTransientError(HeyGenError):
    """Rate limit, 5xx, or a network failure -- worth retrying."""


class HeyGenInsufficientCreditError(HeyGenError):
    """HeyGen's 402 `insufficient_credit` -- the user's wallet is empty."""


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


def _error_code(response: httpx.Response) -> str | None:
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return None
    return error.get("code") if isinstance(error, dict) else None


def _request(
    method: str,
    path: str,
    api_key: str,
    client: httpx.Client | None = None,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    send = client.request if client is not None else httpx.request
    try:
        response = send(
            method,
            f"{base_url()}{path}",
            headers={"X-Api-Key": api_key, **(extra_headers or {})},
            params={k: v for k, v in (params or {}).items() if v is not None},
            json=json,
            timeout=30.0,
        )
    except httpx.TransportError as exc:
        raise HeyGenTransientError(f"HeyGen недоступен: {exc}") from exc

    if response.status_code in (401, 403):
        raise HeyGenAuthError(_error_message(response))
    if response.status_code == 402:
        raise HeyGenInsufficientCreditError(_error_message(response))
    if response.status_code == 409 and _error_code(response) == "request_in_progress":
        # Our own earlier attempt with the same Idempotency-Key is still
        # being processed -- wait and retry, it'll replay that result.
        raise HeyGenTransientError(f"HeyGen: {_error_message(response)}")
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
            # Only private looks carry it: "processing" (still
            # training), "pending_consent" or "failed" can't render yet
            # -- HeyGen answers 409 resource_not_ready.
            "status": look.get("status"),
        }
        for look in body.get("data") or []
        if isinstance(look, dict) and look.get("id")
    ]
    return {"items": items, "next_token": body.get("next_token") if body.get("has_more") else None}


def create_avatar_video(
    api_key: str,
    *,
    avatar_id: str,
    voice_id: str | None,
    script: str,
    aspect_ratio: str,
    idempotency_key: str,
    title: str | None = None,
    client: httpx.Client | None = None,
) -> str:
    """Starts a render and returns its video_id.

    `idempotency_key` is what makes a Celery retry safe for the user's
    wallet: HeyGen replays the original response for the same key for
    24h instead of starting (and billing) a second render, per its
    error-codes docs.

    `voice_id=None` is left out of the body on purpose: with an
    avatar_id, HeyGen then speaks in that avatar's own default voice
    (CreateVideoFromAvatar.voice_id in its schema).
    """
    payload: dict[str, Any] = {
        "type": "avatar",
        "avatar_id": avatar_id,
        "script": script,
        "aspect_ratio": aspect_ratio,
        "title": title,
    }
    if voice_id:
        payload["voice_id"] = voice_id
    body = _request(
        "POST",
        "/v3/videos",
        api_key,
        client,
        json=payload,
        extra_headers={"Idempotency-Key": idempotency_key},
    )
    video_id = (body.get("data") or {}).get("video_id")
    if not video_id:
        raise HeyGenError("HeyGen не вернул video_id")
    return video_id


def get_video(api_key: str, video_id: str, client: httpx.Client | None = None) -> dict[str, Any]:
    path = f"/v3/videos/{quote(video_id, safe='')}"
    return _request("GET", path, api_key, client).get("data") or {}


def list_voices(
    api_key: str,
    token: str | None = None,
    language: str | None = None,
    gender: str | None = None,
    voice_type: str | None = None,
    limit: int = 100,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """`voice_type="private"` lists the user's own cloned voices --
    HeyGen's default is "public" only, so without it a user's own voice
    never shows up."""
    body = _request(
        "GET",
        "/v3/voices",
        api_key,
        client,
        params={
            "token": token,
            "language": language,
            "gender": gender,
            "type": voice_type,
            "limit": limit,
        },
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
