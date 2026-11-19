"""Twitch on «Инструменты»: the streamer connects their account so live
streams read chat over EventSub and can post the avatar's replies."""

import logging

import jwt
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.integrations import (
    find_integration,
    get_secret,
    integration_out,
    upsert_integration,
)
from app.models import IntegrationProvider, User
from app.schemas import IntegrationOut, TwitchConnectRequest, TwitchStartOut
from app.security import create_twitch_oauth_state, decode_twitch_oauth_state
from app.tool_integrations import twitch

router = APIRouter(prefix="/integrations/twitch", tags=["integrations"])
log = logging.getLogger(__name__)


@router.post("/start", response_model=TwitchStartOut)
def start_twitch(current_user: User = Depends(get_current_user)) -> TwitchStartOut:
    settings = get_settings()
    if not settings.twitch_client_id or not settings.twitch_client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Twitch ещё не настроен на сервере",
        )
    return TwitchStartOut(
        authorization_url=twitch.authorization_url(create_twitch_oauth_state(current_user.id))
    )


@router.post("/connect", response_model=IntegrationOut)
def connect_twitch(
    payload: TwitchConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IntegrationOut:
    try:
        state_user = decode_twitch_oauth_state(payload.state)
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Подключение Twitch устарело — начните заново",
        ) from None
    if state_user != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Подключение Twitch было начато другим пользователем",
        )
    try:
        token = twitch.exchange_code(payload.code)
        granted = set(token.get("scope") or [])
        missing = set(twitch.SCOPES) - granted
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Twitch выдал не все разрешения — подключите заново и подтвердите доступ к чату",
            )
        who = twitch.validate(token["access_token"])
    except twitch.TwitchTransientError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except twitch.TwitchError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Не удалось подключить Twitch: {exc}"
        ) from exc
    integration = upsert_integration(
        db,
        current_user,
        IntegrationProvider.twitch,
        token["refresh_token"],
        who["login"],
        settings={"user_id": who["user_id"], "login": who["login"], "scopes": sorted(granted)},
    )
    return integration_out(integration, current_user)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_twitch(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    integration = find_integration(db, current_user, IntegrationProvider.twitch)
    if integration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Twitch не подключён")
    if integration.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Этот Twitch подключил участник команды — отключить его может только он",
        )
    try:
        # Withdraw the grant on Twitch's side too: "disconnect" must mean
        # Cindra can no longer read or write that chat.
        twitch.revoke(get_secret(integration))
    except twitch.TwitchError as exc:
        log.warning("twitch revoke failed: %s", exc)
    db.delete(integration)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
