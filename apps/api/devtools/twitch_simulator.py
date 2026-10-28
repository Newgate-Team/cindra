"""A local stand-in for the parts of Twitch a live stream uses, so the
whole "connect Twitch -> read chat over EventSub -> post replies" path
runs without a Twitch application.

Shapes follow dev.twitch.tv: OAuth authorization-code flow (id.twitch.tv
/oauth2/authorize, /token, /validate, /revoke -- refresh tokens rotate on
every refresh here, to exercise saving the new one), Helix Get Users,
Create EventSub Subscription (websocket transport, must arrive within
10 s of the welcome, 409 for a duplicate) and Send Chat Message, and the
EventSub WebSocket (session_welcome, session_keepalive, notification,
session_reconnect, revocation; close 4003 for an unused connection).

Run: uvicorn devtools.twitch_simulator:app --port 8770, set
TWITCH_BASE_URL=http://localhost:8770 for the API and
TWITCH_EVENTSUB_URL=ws://localhost:8770/ws, TWITCH_HELIX_URL=
http://localhost:8770 for the agent. Drive the chat with POST /sim/chat,
/sim/notice, /sim/delete, /sim/clear_user, /sim/reconnect, /sim/revoke,
/sim/mute (stops keepalives); GET /sim/sent lists posted messages.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

CLIENT_ID = os.environ.get("TWITCH_SIM_CLIENT_ID", "sim-twitch-client")
CLIENT_SECRET = os.environ.get("TWITCH_SIM_CLIENT_SECRET", "sim-twitch-secret")
REDIRECT_URIS = set(os.environ.get(
    "TWITCH_SIM_REDIRECT_URIS",
    "http://localhost:3000/oauth/twitch/callback,http://localhost:3001/oauth/twitch/callback,"
    "https://cindra-chi.vercel.app/oauth/twitch/callback",
).split(","))
USERS = {  # login -> (id, display name)
    "cindra_streamer": ("100001", "Cindra_Streamer"),
    "second_account": ("100002", "Second_Account"),
    "kaicenat": ("200001", "KaiCenat"),
    "viewer_one": ("300001", "Viewer_One"),
}
CHAT_TYPES = {
    "channel.chat.message", "channel.chat.notification", "channel.chat.message_delete",
    "channel.chat.clear_user_messages", "channel.chat.clear",
}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class _Grant:
    login: str
    scopes: list[str]
    revoked: bool = False


@dataclass
class _Session:
    id: str
    ws: WebSocket
    welcomed_at: float
    keepalive: int
    subscriptions: list[dict[str, Any]] = field(default_factory=list)
    muted: bool = False
    closed: bool = False


class _State:
    def __init__(self) -> None:
        self.codes: dict[str, dict[str, Any]] = {}
        self.refresh: dict[str, _Grant] = {}
        self.access: dict[str, tuple[_Grant, float]] = {}
        self.sessions: dict[str, _Session] = {}
        self.sent: list[dict[str, Any]] = []
        self.inherited: dict[str, list[dict[str, Any]]] = {}


state = _State()
app = FastAPI(title="Twitch simulator")


def _error(status: int, message: str, error: str = "Bad Request") -> JSONResponse:
    return JSONResponse({"error": error, "status": status, "message": message}, status_code=status)


# ---- OAuth ------------------------------------------------------------------


@app.get("/oauth2/authorize", response_class=HTMLResponse)
def authorize(request: Request) -> Response:
    q = request.query_params
    if q.get("client_id") != CLIENT_ID:
        return HTMLResponse("<h1>invalid client</h1>", status_code=400)
    if q.get("redirect_uri") not in REDIRECT_URIS:
        return HTMLResponse("<h1>redirect_mismatch</h1>", status_code=400)
    if q.get("response_type") != "code":
        return HTMLResponse("<h1>unsupported_response_type</h1>", status_code=400)
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in q.items()
    )
    accounts = "".join(
        f'<label><input type="radio" name="login" value="{login}"{" checked" if i == 0 else ""}>'
        f" {name}</label><br>"
        for i, (login, (_, name)) in enumerate(list(USERS.items())[:2])
    )
    scopes = html.escape(q.get("scope", ""))
    return HTMLResponse(
        f"""<!doctype html><meta charset="utf-8"><title>Twitch (симулятор)</title>
<body style="font-family:system-ui;max-width:520px;margin:40px auto">
<p style="color:#888">Симулятор Twitch — не настоящий Twitch</p>
<h1>Cindra запрашивает доступ к вашему аккаунту Twitch</h1>
<p>Разрешения: <code>{scopes}</code></p>
<form method="post" action="/oauth2/approve">{hidden}{accounts}
<button name="decision" value="allow">Разрешить</button>
<button name="decision" value="deny">Отмена</button></form></body>"""
    )


@app.post("/oauth2/approve")
async def approve(request: Request) -> Response:
    form = dict(await request.form())
    redirect = form.get("redirect_uri", "")
    params = {"state": form.get("state", "")}
    if form.get("decision") != "allow":
        params.update(error="access_denied", error_description="The user denied you access")
    else:
        code = secrets.token_urlsafe(20)
        state.codes[code] = {"login": form.get("login") or "cindra_streamer",
                             "scopes": form.get("scope", "").split(), "redirect": redirect,
                             "at": time.time()}
        params.update(code=code, scope=form.get("scope", ""))
    return RedirectResponse(f"{redirect}?{urlencode(params)}", status_code=302)


def _issue(grant: _Grant) -> dict[str, Any]:
    access = secrets.token_urlsafe(22)
    state.access[access] = (grant, time.time() + 14400)
    refresh = secrets.token_urlsafe(40)
    state.refresh[refresh] = grant
    return {"access_token": access, "expires_in": 14400, "refresh_token": refresh,
            "scope": grant.scopes, "token_type": "bearer"}


@app.post("/oauth2/token")
async def token(request: Request) -> Response:
    form = dict(await request.form())
    if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
        return _error(403, "invalid client secret", "Forbidden")
    if form.get("grant_type") == "authorization_code":
        code = state.codes.pop(form.get("code", ""), None)
        if code is None or time.time() - code["at"] > 600:
            return _error(400, "Invalid authorization code")
        if form.get("redirect_uri") != code["redirect"]:
            return _error(400, "Parameter redirect_uri does not match registered URI")
        return JSONResponse(_issue(_Grant(code["login"], code["scopes"])))
    if form.get("grant_type") == "refresh_token":
        # Rotates: the old refresh token stops working.
        grant = state.refresh.pop(form.get("refresh_token", ""), None)
        if grant is None or grant.revoked:
            return _error(400, "Invalid refresh token")
        return JSONResponse(_issue(grant))
    return _error(400, "Invalid grant type")


def _grant_for(access_token: str) -> _Grant | None:
    entry = state.access.get(access_token)
    if entry is None or entry[0].revoked or entry[1] < time.time():
        return None
    return entry[0]


@app.get("/oauth2/validate")
def validate(request: Request) -> Response:
    auth = request.headers.get("authorization", "")
    grant = _grant_for(auth.removeprefix("OAuth ").strip()) if auth.startswith("OAuth ") else None
    if grant is None:
        return JSONResponse({"status": 401, "message": "invalid access token"}, status_code=401)
    user_id, _ = USERS[grant.login]
    return JSONResponse({"client_id": CLIENT_ID, "login": grant.login, "scopes": grant.scopes,
                         "user_id": user_id, "expires_in": 14000})


@app.post("/oauth2/revoke")
async def revoke(request: Request) -> Response:
    form = dict(await request.form())
    if form.get("client_id") != CLIENT_ID:
        return _error(400, "Invalid client id")
    tok = form.get("token", "")
    grant = state.refresh.get(tok) or (state.access.get(tok) or (None,))[0]
    if grant is None:
        return _error(400, "Invalid token")
    grant.revoked = True
    return Response(status_code=200)


# ---- Helix --------------------------------------------------------------------


def _helix_grant(request: Request) -> _Grant | JSONResponse:
    if request.headers.get("client-id") != CLIENT_ID:
        return _error(401, "Client ID and OAuth token do not match", "Unauthorized")
    auth = request.headers.get("authorization", "")
    grant = _grant_for(auth.removeprefix("Bearer ").strip())
    if grant is None:
        return _error(401, "Invalid OAuth token", "Unauthorized")
    return grant


@app.get("/helix/users")
def users(request: Request) -> Response:
    if isinstance(grant := _helix_grant(request), JSONResponse):
        return grant
    found = [
        {"id": USERS[login][0], "login": login, "display_name": USERS[login][1], "type": "",
         "broadcaster_type": "", "description": "", "profile_image_url": "",
         "offline_image_url": "", "view_count": 0, "created_at": "2020-01-01T00:00:00Z"}
        for login in request.query_params.getlist("login") if login in USERS
    ]
    return JSONResponse({"data": found})


@app.post("/helix/eventsub/subscriptions")
async def subscribe(request: Request) -> Response:
    if isinstance(grant := _helix_grant(request), JSONResponse):
        return grant
    body = await request.json()
    kind, transport = body.get("type"), body.get("transport") or {}
    condition = body.get("condition") or {}
    if kind not in CHAT_TYPES or body.get("version") != "1":
        return _error(400, f"unsupported subscription type {kind!r} version {body.get('version')!r}")
    if transport.get("method") != "websocket":
        return _error(400, "transport.method must be websocket for a user token")
    if "user:read:chat" not in grant.scopes:
        return _error(403, "subscription missing proper authorization", "Forbidden")
    if condition.get("user_id") != USERS[grant.login][0]:
        return _error(403, "user_id must match the token's user", "Forbidden")
    if condition.get("broadcaster_user_id") not in {u[0] for u in USERS.values()}:
        return _error(400, "invalid broadcaster_user_id")
    session = state.sessions.get(transport.get("session_id", ""))
    if session is None or session.closed:
        return _error(400, "websocket transport session does not exist or has already disconnected")
    if any(s["type"] == kind and s["condition"] == condition for s in session.subscriptions):
        return _error(409, "subscription already exists", "Conflict")
    sub = {"id": str(uuid.uuid4()), "status": "enabled", "type": kind, "version": "1",
           "condition": condition, "created_at": _now(), "cost": 0,
           "transport": {"method": "websocket", "session_id": session.id, "connected_at": _now()}}
    session.subscriptions.append(sub)
    return JSONResponse({"data": [sub], "total": len(session.subscriptions), "total_cost": 0,
                         "max_total_cost": 10}, status_code=202)


@app.post("/helix/chat/messages")
async def send_chat(request: Request) -> Response:
    if isinstance(grant := _helix_grant(request), JSONResponse):
        return grant
    body = await request.json()
    if "user:write:chat" not in grant.scopes:
        return _error(401, "Missing scope: user:write:chat", "Unauthorized")
    if body.get("sender_id") != USERS[grant.login][0]:
        return _error(403, "The sender must match the user ID in the access token", "Forbidden")
    text = str(body.get("message", ""))
    if not text or len(text) > 500:
        return _error(400, "The message is required and must be at most 500 characters")
    message_id = str(uuid.uuid4())
    state.sent.append({"broadcaster_id": body.get("broadcaster_id"), "sender": grant.login,
                       "message": text, "id": message_id})
    channel = next((login for login, (uid, _) in USERS.items()
                    if uid == body.get("broadcaster_id")), "")
    # Twitch echoes your own message back into the chat feed.
    await _broadcast("channel.chat.message", _message_event(channel, grant.login, text, message_id))
    return JSONResponse({"data": [{"message_id": message_id, "is_sent": True, "drop_reason": None}]})


# ---- EventSub WebSocket ---------------------------------------------------------


def _envelope(kind: str, payload: dict[str, Any], sub_type: str | None = None) -> str:
    metadata: dict[str, Any] = {"message_id": str(uuid.uuid4()), "message_type": kind,
                                "message_timestamp": _now()}
    if sub_type:
        metadata.update(subscription_type=sub_type, subscription_version="1")
    return json.dumps({"metadata": metadata, "payload": payload})


def _session_payload(session: _Session, reconnect_url: str | None = None) -> dict[str, Any]:
    return {"session": {"id": session.id, "status": "reconnecting" if reconnect_url else "connected",
                        "connected_at": _now(), "keepalive_timeout_seconds":
                        None if reconnect_url else session.keepalive,
                        "reconnect_url": reconnect_url, "recovery_url": None}}


@app.websocket("/ws")
async def eventsub(websocket: WebSocket) -> None:
    await websocket.accept()
    keepalive = int(websocket.query_params.get("keepalive_timeout_seconds", "10"))
    if not 10 <= keepalive <= 600:
        await websocket.close(code=4000)
        return
    session = _Session(id=secrets.token_hex(12), ws=websocket, welcomed_at=time.time(),
                       keepalive=keepalive)
    inherit = websocket.query_params.get("from")
    if inherit and inherit in state.inherited:
        session.subscriptions = state.inherited.pop(inherit)
    state.sessions[session.id] = session
    await websocket.send_text(_envelope("session_welcome", _session_payload(session)))
    last_sent = time.time()
    try:
        while not session.closed:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                # Clients must not send anything: Twitch closes with 4001.
                await websocket.close(code=4001)
                break
            except TimeoutError:
                pass
            if not session.subscriptions and time.time() - session.welcomed_at > 10:
                await websocket.close(code=4003)  # connection unused
                break
            if not session.muted and time.time() - last_sent >= session.keepalive * 0.8:
                await websocket.send_text(_envelope("session_keepalive", {}))
                last_sent = time.time()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        session.closed = True


def _message_event(channel: str, login: str, text: str, message_id: str, *,
                   bits: int = 0, first: bool = False, badges: list[str] | None = None) -> dict:
    broadcaster_id, broadcaster_name = USERS.get(channel, ("0", channel))
    user_id, user_name = USERS.get(login, ("999", login))
    return {
        "broadcaster_user_id": broadcaster_id, "broadcaster_user_login": channel,
        "broadcaster_user_name": broadcaster_name,
        "chatter_user_id": user_id, "chatter_user_login": login, "chatter_user_name": user_name,
        "message_id": message_id,
        "message": {"text": text, "fragments": [{"type": "text", "text": text, "cheermote": None,
                                                 "emote": None, "mention": None}]},
        "color": "#1E90FF",
        "badges": [{"set_id": b, "id": "1", "info": ""} for b in badges or []],
        "message_type": "user_intro" if first else "text",
        "cheer": {"bits": bits} if bits else None,
        "reply": None, "channel_points_custom_reward_id": None,
        "source_broadcaster_user_id": None, "source_broadcaster_user_login": None,
        "source_broadcaster_user_name": None, "source_message_id": None, "source_badges": None,
    }


async def _broadcast(sub_type: str, event: dict[str, Any]) -> int:
    delivered = 0
    for session in list(state.sessions.values()):
        if session.closed:
            continue
        for sub in session.subscriptions:
            if sub["type"] == sub_type and \
                    sub["condition"].get("broadcaster_user_id") == event.get("broadcaster_user_id"):
                with contextlib.suppress(Exception):
                    await session.ws.send_text(_envelope(
                        "notification", {"subscription": sub, "event": event}, sub_type))
                    delivered += 1
    return delivered


@app.post("/sim/chat")
async def sim_chat(request: Request) -> dict:
    b = await request.json()
    mid = b.get("message_id") or str(uuid.uuid4())
    event = _message_event(b["channel"], b["user"], b["text"], mid, bits=b.get("bits", 0),
                           first=b.get("first", False), badges=b.get("badges"))
    return {"delivered": await _broadcast("channel.chat.message", event), "message_id": mid}


@app.post("/sim/notice")
async def sim_notice(request: Request) -> dict:
    b = await request.json()
    broadcaster_id, name = USERS.get(b["channel"], ("0", b["channel"]))
    user_id, user_name = USERS.get(b["user"], ("999", b["user"]))
    kind = b["notice_type"]
    event = {
        "broadcaster_user_id": broadcaster_id, "broadcaster_user_login": b["channel"],
        "broadcaster_user_name": name, "chatter_user_id": user_id, "chatter_user_login": b["user"],
        "chatter_user_name": user_name, "chatter_is_anonymous": False, "color": "",
        "badges": [], "system_message": b.get("system_message", f"{user_name} {kind}"),
        "message_id": str(uuid.uuid4()), "message": {"text": "", "fragments": []},
        "notice_type": kind,
        "sub": None, "resub": None, "sub_gift": None, "community_sub_gift": None,
        "gift_paid_upgrade": None, "prime_paid_upgrade": None, "pay_it_forward": None,
        "raid": None, "unraid": None, "announcement": None, "bits_badge_tier": None,
        "charity_donation": None,
    }
    if kind == "raid":
        event["raid"] = {"user_id": user_id, "user_name": user_name, "user_login": b["user"],
                         "viewer_count": b.get("viewer_count", 1), "profile_image_url": ""}
    elif kind in event:
        event[kind] = {"sub_tier": "1000", "is_prime": False, "duration_months": 1}
    return {"delivered": await _broadcast("channel.chat.notification", event)}


@app.post("/sim/delete")
async def sim_delete(request: Request) -> dict:
    b = await request.json()
    broadcaster_id, name = USERS.get(b["channel"], ("0", b["channel"]))
    target_id, target_name = USERS.get(b["user"], ("999", b["user"]))
    event = {"broadcaster_user_id": broadcaster_id, "broadcaster_user_login": b["channel"],
             "broadcaster_user_name": name, "target_user_id": target_id,
             "target_user_login": b["user"], "target_user_name": target_name,
             "message_id": b["message_id"]}
    return {"delivered": await _broadcast("channel.chat.message_delete", event)}


@app.post("/sim/clear_user")
async def sim_clear_user(request: Request) -> dict:
    b = await request.json()
    broadcaster_id, name = USERS.get(b["channel"], ("0", b["channel"]))
    target_id, target_name = USERS.get(b["user"], ("999", b["user"]))
    event = {"broadcaster_user_id": broadcaster_id, "broadcaster_user_login": b["channel"],
             "broadcaster_user_name": name, "target_user_id": target_id,
             "target_user_login": b["user"], "target_user_name": target_name}
    return {"delivered": await _broadcast("channel.chat.clear_user_messages", event)}


@app.post("/sim/reconnect")
async def sim_reconnect(request: Request) -> dict:
    """Tells every live session to move; the new one keeps the subscriptions."""
    base = str(request.base_url).replace("http", "ws", 1).rstrip("/")
    moved = 0
    for session in list(state.sessions.values()):
        if session.closed:
            continue
        state.inherited[session.id] = session.subscriptions
        url = f"{base}/ws?from={session.id}"
        with contextlib.suppress(Exception):
            await session.ws.send_text(_envelope("session_reconnect", _session_payload(session, url)))
            moved += 1
    return {"moved": moved}


@app.post("/sim/revoke")
async def sim_revoke(request: Request) -> dict:
    b = await request.json()
    for grant in list(state.refresh.values()) + [g for g, _ in state.access.values()]:
        if grant.login == b["login"]:
            grant.revoked = True
    revoked = 0
    for session in list(state.sessions.values()):
        for sub in session.subscriptions:
            if sub["condition"].get("user_id") == USERS[b["login"]][0] and not session.closed:
                with contextlib.suppress(Exception):
                    await session.ws.send_text(_envelope(
                        "revocation", {"subscription": {**sub, "status": "authorization_revoked"}},
                        sub["type"]))
                    revoked += 1
    return {"revoked": revoked}


@app.post("/sim/mute")
def sim_mute() -> dict:
    for session in state.sessions.values():
        session.muted = True
    return {"muted": len(state.sessions)}


@app.get("/sim/sent")
def sim_sent() -> list[dict[str, Any]]:
    return state.sent


@app.post("/sim/reset")
def sim_reset() -> Response:
    state.__init__()
    return Response(status_code=204)
