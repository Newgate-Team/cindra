"""A local stand-in for HeyGen's v3 API, for running Cindra's whole
HeyGen flow without a real (paid) key.

Every request is validated against HeyGen's own OpenAPI schemas
(heygen_contract.py), and every response it sends is built to pass
them too -- so it can't quietly accept something real HeyGen would
reject. It mimics the behaviour Cindra depends on: key auth, wallet
balance, public + private avatars (including one still training),
public + cloned voices, Idempotency-Key replay/conflict, a render that
moves pending -> processing -> completed, and a downloadable file.

Run locally:
    uvicorn devtools.heygen_simulator:app --port 8765
and point the API/worker at it with HEYGEN_API_BASE_URL=http://localhost:8765.

Keys: anything starting with "sim_" is valid; "sim_broke" has an empty
wallet (renders get HeyGen's 402 insufficient_credit). A script
containing "[fail]" produces a failed render. Render time is
HEYGEN_SIM_RENDER_SECONDS (default 8).

Refreshing the vendored schema after HeyGen changes its API: download
https://developers.heygen.com/openapi/external-api.json and re-cut the
same five paths (and the components they reference) into
heygen_v3_subset.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from functools import cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from devtools import heygen_contract as contract

_DOC_URL = "https://developers.heygen.com/docs/error-codes"

_PUBLIC_LOOKS = [
    ("lk_pub_anna", "Анна — офис", "studio_avatar", "female", "voice_pub_ru_f"),
    ("lk_pub_ivan", "Иван — студия", "studio_avatar", "male", "voice_pub_ru_m"),
    ("lk_pub_kate", "Kate — casual", "photo_avatar", "female", "voice_pub_en_f"),
]
_PRIVATE_LOOKS = [
    ("lk_my_twin", "Мой цифровой двойник", "digital_twin", "male", "voice_my_clone", "completed"),
    ("lk_my_new", "Новый образ", "digital_twin", "male", None, "processing"),
]
_PUBLIC_VOICES = [
    ("voice_pub_ru_f", "Мария", "Russian", "female"),
    ("voice_pub_ru_m", "Алексей", "Russian", "male"),
    ("voice_pub_en_f", "Sara", "English", "female"),
    ("voice_pub_en_m", "Tom", "English", "male"),
]
_PRIVATE_VOICES = [("voice_my_clone", "Мой голос (клон)", "Russian", "male")]


class _State:
    def __init__(self) -> None:
        self.videos: dict[str, dict[str, Any]] = {}
        # (api_key, idempotency_key) -> (request fingerprint, response body)
        self.idempotency: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        self.spent: dict[str, float] = {}


state = _State()
app = FastAPI(title="HeyGen v3 simulator (Cindra devtools)")


def _render_seconds() -> float:
    return float(os.environ.get("HEYGEN_SIM_RENDER_SECONDS", "8"))


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "doc_url": f"{_DOC_URL}#{code}"}},
    )


def _auth(request: Request) -> str | JSONResponse:
    key = request.headers.get("x-api-key", "")
    if not key.startswith("sim_"):
        return _error(401, "unauthorized", "Unauthorized")
    return key


def _invalid(errors: list[str]) -> JSONResponse:
    return _error(400, "invalid_parameter", "; ".join(errors))


def _checked(path: str, method: str, body: dict[str, Any]) -> JSONResponse:
    """Refuses to send anything real HeyGen's schema wouldn't allow."""
    errors = contract.response_errors(path, method, 200, body)
    if errors:
        raise AssertionError(f"simulator response breaks HeyGen's schema: {errors}")
    return JSONResponse(content=body)


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _balance(key: str) -> float:
    return 0.0 if key == "sim_broke" else round(50.0 - state.spent.get(key, 0.0), 2)


def _paginate(items: list[dict[str, Any]], request: Request, default_limit: int) -> dict:
    limit = int(request.query_params.get("limit", default_limit))
    start = int(request.query_params.get("token") or 0)
    page = items[start : start + limit]
    has_more = start + limit < len(items)
    return {"data": page, "has_more": has_more, "next_token": str(start + limit) if has_more else None}


@app.get("/v3/users/me")
def users_me(request: Request) -> Response:
    key = _auth(request)
    if isinstance(key, JSONResponse):
        return key
    return _checked(
        "/v3/users/me",
        "get",
        {
            "data": {
                "username": "sim_user",
                "email": "simulator@heygen.local",
                "first_name": "Sim",
                "last_name": "User",
                "billing_type": "wallet",
                "wallet": {
                    "currency": "usd",
                    "remaining_balance": _balance(key),
                    "auto_reload": {"enabled": False},
                },
                "subscription": None,
                "usage_based": None,
            }
        },
    )


def _look(base: str, look_id: str, name: str, kind: str, gender: str, voice: str | None,
          status: str | None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": look_id,
        "name": name,
        "avatar_type": kind,
        "gender": gender,
        "preview_image_url": f"{base}/files/look/{look_id}.svg",
        "preview_video_url": None,
        "default_voice_id": voice,
        "tags": [],
        "supported_api_engines": ["avatar_iv"],
    }
    if status is not None:
        item["status"] = status
    return item


@app.get("/v3/avatars/looks")
def avatar_looks(request: Request) -> Response:
    key = _auth(request)
    if isinstance(key, JSONResponse):
        return key
    errors = contract.parameter_errors(
        "/v3/avatars/looks", "get", dict(request.query_params), dict(request.headers)
    )
    if errors:
        return _invalid(errors)
    base = _base(request)
    public = [_look(base, *row, status=None) for row in _PUBLIC_LOOKS]
    private = [_look(base, *row[:5], status=row[5]) for row in _PRIVATE_LOOKS]
    ownership = request.query_params.get("ownership")
    items = private if ownership == "private" else public if ownership == "public" else private + public
    return _checked("/v3/avatars/looks", "get", _paginate(items, request, default_limit=20))


@app.get("/v3/voices")
def voices(request: Request) -> Response:
    key = _auth(request)
    if isinstance(key, JSONResponse):
        return key
    errors = contract.parameter_errors(
        "/v3/voices", "get", dict(request.query_params), dict(request.headers)
    )
    if errors:
        return _invalid(errors)
    voice_type = request.query_params.get("type", "public")
    rows = _PRIVATE_VOICES if voice_type == "private" else _PUBLIC_VOICES
    language = request.query_params.get("language")
    gender = request.query_params.get("gender")
    items = [
        {
            "voice_id": voice_id,
            "name": name,
            "language": lang,
            "gender": sex,
            "preview_audio_url": None,
            "support_pause": True,
            "support_locale": False,
            "type": voice_type,
            "default_engine": None,
            "available_engines": [],
        }
        for voice_id, name, lang, sex in rows
        if (language is None or lang == language) and (gender is None or sex == gender)
    ]
    return _checked("/v3/voices", "get", _paginate(items, request, default_limit=20))


def _all_looks() -> dict[str, tuple]:
    looks = {row[0]: (*row, "completed") for row in _PUBLIC_LOOKS}
    looks.update({row[0]: row for row in _PRIVATE_LOOKS})
    return looks


@app.post("/v3/videos")
async def create_video(request: Request) -> Response:
    key = _auth(request)
    if isinstance(key, JSONResponse):
        return key
    errors = contract.parameter_errors(
        "/v3/videos", "post", dict(request.query_params), dict(request.headers)
    )
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _invalid(["request body is not JSON"])
    errors += contract.request_body_errors("/v3/videos", "post", body)
    if errors:
        return _invalid(errors)

    idem_key = request.headers.get("idempotency-key")
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    if idem_key and (key, idem_key) in state.idempotency:
        seen_fingerprint, seen_response = state.idempotency[(key, idem_key)]
        if seen_fingerprint != fingerprint:
            return _error(409, "conflict", "Idempotency-Key was already used with a different body")
        return JSONResponse(content=seen_response)

    look = _all_looks().get(body["avatar_id"])
    if look is None:
        return _error(404, "avatar_not_found", "Verify that the avatar_id is correct")
    if look[-1] != "completed":
        return _error(409, "resource_not_ready", "This avatar is still processing")
    if body.get("script") and not (body.get("voice_id") or look[4]):
        return _invalid(["voice_id is required: this avatar has no default voice"])
    if _balance(key) <= 0:
        return _error(402, "insufficient_credit", "Your account does not have enough credits")

    video_id = f"v_sim_{uuid.uuid4().hex[:12]}"
    state.videos[video_id] = {
        "key": key,
        "created": time.time(),
        "title": body.get("title"),
        "fail": "[fail]" in (body.get("script") or ""),
    }
    state.spent[key] = state.spent.get(key, 0.0) + 1.0
    response = {"data": {"video_id": video_id, "status": "waiting", "output_format": "mp4"}}
    if idem_key:
        state.idempotency[(key, idem_key)] = (fingerprint, response)
    return JSONResponse(content=response)


@app.get("/v3/videos/{video_id}")
def get_video(video_id: str, request: Request) -> Response:
    key = _auth(request)
    if isinstance(key, JSONResponse):
        return key
    video = state.videos.get(video_id)
    if video is None or video["key"] != key:
        return _error(404, "not_found", "Video not found.")
    elapsed = time.time() - video["created"]
    total = _render_seconds()
    detail: dict[str, Any] = {
        "id": video_id,
        "title": video["title"],
        "created_at": int(video["created"]),
        "video_page_url": f"https://app.heygen.com/videos/{video_id}",
    }
    if elapsed < total * 0.3:
        detail["status"] = "pending"
    elif elapsed < total:
        detail["status"] = "processing"
    elif video["fail"]:
        detail.update(
            status="failed",
            failure_code="render_failed",
            failure_message="Simulated render failure ([fail] in script)",
        )
    else:
        base = _base(request)
        detail.update(
            status="completed",
            completed_at=int(video["created"] + total),
            # Presigned-looking, like the real thing (docs: "Presigned
            # URL to download the video file").
            video_url=f"{base}/files/video/{video_id}.mp4?Expires={int(time.time()) + 604800}",
            thumbnail_url=f"{base}/files/look/lk_pub_anna.svg",
            duration=4.0,
        )
    return _checked("/v3/videos/{video_id}", "get", {"data": detail})


@cache
def _sample_mp4() -> bytes:
    """A real, playable 4s clip when ffmpeg is installed (local runs),
    otherwise a placeholder -- tests only check the bytes round-trip."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return b"\x00\x00\x00\x18ftypmp42simulated-heygen-video"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "sample.mp4"
        subprocess.run(
            [
                ffmpeg, "-loglevel", "error", "-f", "lavfi",
                "-i", "testsrc2=size=540x960:rate=25:duration=4",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-shortest", "-movflags", "+faststart", str(out),
            ],
            check=True,
        )
        return out.read_bytes()


@app.get("/files/video/{name}")
def video_file(name: str) -> Response:
    if name.removesuffix(".mp4") not in state.videos:
        return Response(status_code=404)
    return Response(content=_sample_mp4(), media_type="video/mp4")


@app.get("/files/look/{name}")
def look_preview(name: str) -> Response:
    label = name.removesuffix(".svg").replace("lk_", "")[:12]
    hue = int(hashlib.md5(label.encode()).hexdigest()[:2], 16) * 360 // 256
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">'
        f'<rect width="120" height="120" fill="hsl({hue},45%,35%)"/>'
        '<circle cx="60" cy="46" r="22" fill="#f1d3b3"/>'
        '<rect x="28" y="74" width="64" height="46" rx="22" fill="#f1d3b3"/>'
        f'<text x="60" y="114" font-size="11" text-anchor="middle" fill="#fff">{label}</text>'
        "</svg>"
    )
    return Response(content=svg, media_type="image/svg+xml")
