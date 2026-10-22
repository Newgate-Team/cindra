"""Simli (api.simli.ai): the cheaper real-time avatar vendor for live
streams, on the user's own key (x-simli-api-key). The API side checks
the key and lists faces; the stream agent runs the sessions.

Simli has no "who am I" endpoint, so a key is checked with the cheapest
authenticated call, GET /ratelimiter/sessions (401 for a bad key).
GET /faces returns only the account's own faces, without names or
pictures; the ready-made ones come from Simli's published preset list.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings

_RETRYABLE = {429, 500, 502, 503, 504}

# docs.simli.com/api-reference/preset-faces (Trinity-2 faces), 2026-10.
_PRESETS = (
    ("3bad681f-1b4a-4507-bc54-fbc675c191b7", "Victor", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/3bad681f-1b4a-4507-bc54-fbc675c191b7.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=20f5a9d61865fc4ff54eec1c6ab877a6"),
    ("8ef039c1-06d7-4ea7-b01c-6b620c755a92", "Ruby", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/8ef039c1-06d7-4ea7-b01c-6b620c755a92.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=e424a8aa55071a52341594b1fe8ee338"),
    ("1d032d8b-0cff-4156-9f0d-f001f0738d9c", "Margaret", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/1d032d8b-0cff-4156-9f0d-f001f0738d9c.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=104d372712c5c69c39f974f36a319989"),
    ("80459b57-2d66-4ca7-8682-59e1c649bc53", "Elena", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/80459b57-2d66-4ca7-8682-59e1c649bc53.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=63b9b787e21071df558ed26cb5ff1375"),
    ("4cfcd276-d8c0-4652-bb7c-2096815d13f0", "Charlotte", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/4cfcd276-d8c0-4652-bb7c-2096815d13f0.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=e7b13d66fa10ca1c95fafb86d62b5e30"),
    ("8791d4d3-2924-49c8-be56-ada2f0d8b3fe", "Cleopatra", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/8791d4d3-2924-49c8-be56-ada2f0d8b3fe.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=5773d9c2b40c34346280f7bbda1159c2"),
    ("efcb440b-6a24-4e79-a46a-764c1d4b751d", "Caesar", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/efcb440b-6a24-4e79-a46a-764c1d4b751d.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=32e4430abeb0b024bd410320975b78c9"),
    ("a6d08e06-038a-4b32-84ae-b02e0f0f1140", "Zahra", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/a6d08e06-038a-4b32-84ae-b02e0f0f1140.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=df7b102bae6e6911707421e5b63fe988"),
    ("03ce3ee8-db97-4150-a333-8f643d48fee9", "Daniel", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/03ce3ee8-db97-4150-a333-8f643d48fee9.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=0323f127d91d1556a899c17f587626a8"),
    ("66d1f5af-9b9b-49e2-a97f-6f5f3d59a2d5", "Tina", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/66d1f5af-9b9b-49e2-a97f-6f5f3d59a2d5.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=1ce9f04646d5c6b9881ce2beb7ce2053"),
    ("fb78d7f5-37ad-4b04-9789-8712dcaa791d", "Big Foot", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/fb78d7f5-37ad-4b04-9789-8712dcaa791d.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=777de0b20531bc28bbacd33edfe332af"),
    ("c1cf7312-5b70-454b-a3c2-a0b0b7e32caf", "Fred", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/c1cf7312-5b70-454b-a3c2-a0b0b7e32caf.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=9f2834b0b24112590b31c8506d0dc949"),
    ("9291a1e2-8290-43ab-b5e1-4a5cdf147402", "Doctor", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/9291a1e2-8290-43ab-b5e1-4a5cdf147402.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=82c68fe8f50d2dbc74d48a5adff1aea8"),
    ("9fcd5739-bf66-49a5-b525-d02d7f0bc2fc", "Hank", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/9fcd5739-bf66-49a5-b525-d02d7f0bc2fc.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=e3dcfafffa550f3d4fd60211adbc204b"),
    ("2e0f3737-1b57-4e43-951e-2ec1c88f01c5", "Sabour", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/2e0f3737-1b57-4e43-951e-2ec1c88f01c5.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=5cc3c81ce79f3f6e01e3e78303601d2f"),
    ("8b1a04c8-b548-452f-bf7f-04907ea9720f", "Lina", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/8b1a04c8-b548-452f-bf7f-04907ea9720f.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=9c55393c10a2dea6c3166b87c6383f49"),
    ("99126e69-c224-404c-9aed-80805c1c9a94", "Sophie", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/99126e69-c224-404c-9aed-80805c1c9a94.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=cb942b07b332ba5184881c35f4e69eed"),
    ("e44041c0-f785-4904-9ddf-4bef9b94d4cc", "Nonna", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/e44041c0-f785-4904-9ddf-4bef9b94d4cc.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=267335442435ccf7caf84da6f22f247c"),
    ("3b1438ce-507a-4f6e-a9f5-9e72531d85bc", "Tony", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/3b1438ce-507a-4f6e-a9f5-9e72531d85bc.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=a1fa6b2eb3946571281b9419b94d2a33"),
    ("c060fe44-9b14-43e8-b92c-83a0e230db11", "Kate", "https://mintcdn.com/simli/qbyFkm9Sv84EiPsu/images/trinity2/c060fe44-9b14-43e8-b92c-83a0e230db11.jpg?fit=max&auto=format&n=qbyFkm9Sv84EiPsu&q=85&s=c20e4b7bea8c4b3278e8592e4651b4e7"),
)


class SimliError(Exception):
    pass


class SimliAuthError(SimliError):
    pass


class SimliTransientError(SimliError):
    pass


def base_url() -> str:
    return get_settings().simli_api_base_url.rstrip("/")


def _get(api_key: str, path: str, client: httpx.Client | None) -> Any:
    send = client.get if client is not None else httpx.get
    try:
        response = send(
            f"{base_url()}{path}", headers={"x-simli-api-key": api_key}, timeout=20.0
        )
    except httpx.TransportError as exc:
        raise SimliTransientError(f"Simli недоступен: {exc}") from exc
    if response.status_code in (401, 403):
        raise SimliAuthError("Simli не принял ключ")
    if response.status_code in _RETRYABLE:
        raise SimliTransientError(f"Simli {response.status_code}")
    if response.status_code >= 400:
        raise SimliError(f"Simli {response.status_code}: {response.text[:200]}")
    return response.json()


def active_sessions(api_key: str, client: httpx.Client | None = None) -> int:
    """Also the key check: the cheapest authenticated call."""
    return int(_get(api_key, "/ratelimiter/sessions", client).get("currentUsage", 0))


def list_faces(api_key: str, *, public: bool, client: httpx.Client | None = None) -> dict:
    """Same shape as the LiveAvatar avatar list, so one picker serves both."""
    if public:
        items = [
            {"id": fid, "name": name, "preview_url": url, "status": "ACTIVE",
             "type": "TRINITY", "default_voice_name": None, "ready": True}
            for fid, name, url in _PRESETS
        ]
        return {"items": items, "next_page": None}
    faces = _get(api_key, "/faces", client)
    items = [
        {
            "id": face["id"],
            # No names in Simli's API: the creation date tells them apart.
            "name": f"Моё лицо от {str(face.get('created_at', ''))[:10]}",
            "preview_url": None,
            "status": "ACTIVE",
            "type": f"v{face.get('simli_version', '?')}",
            "default_voice_name": None,
            "ready": True,
        }
        for face in faces
        if isinstance(face, dict) and face.get("id")
    ]
    return {"items": items, "next_page": None}
