"""LiveAvatar (HeyGen's real-time avatars) on «Инструменты»: connect a
key, see credits, pick the avatar that will host a live stream."""

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.integrations import (
    find_integration,
    get_secret,
    integration_out,
    upsert_integration,
)
from app.models import IntegrationProvider, User
from app.schemas import (
    IntegrationOut,
    LiveAvatarAccountOut,
    LiveAvatarAvatarPage,
    LiveAvatarConnectRequest,
)
from app.tool_integrations import liveavatar

router = APIRouter(prefix="/integrations/liveavatar", tags=["integrations"])

_STORED_KEY_REJECTED = "Ключ LiveAvatar недействителен или отозван — подключите LiveAvatar заново"


def _call[T](fn: Callable[[], T], auth_message: str = _STORED_KEY_REJECTED) -> T:
    try:
        return fn()
    except liveavatar.LiveAvatarAuthError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=auth_message) from None
    except liveavatar.LiveAvatarTransientError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except liveavatar.LiveAvatarError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


def _key(db: Session, user: User) -> str:
    integration = find_integration(db, user, IntegrationProvider.liveavatar)
    if integration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="LiveAvatar не подключён")
    return get_secret(integration)


@router.put("", response_model=IntegrationOut)
def connect_liveavatar(
    payload: LiveAvatarConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IntegrationOut:
    _call(
        lambda: liveavatar.get_credits(payload.api_key),
        auth_message="LiveAvatar не принял этот ключ. Нужен ключ именно LiveAvatar "
        "(app.liveavatar.com → Developers), ключ HeyGen API здесь не подойдёт",
    )
    integration = upsert_integration(
        db, current_user, IntegrationProvider.liveavatar, payload.api_key, "LiveAvatar"
    )
    return integration_out(integration, current_user)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_liveavatar(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    integration = find_integration(db, current_user, IntegrationProvider.liveavatar)
    if integration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="LiveAvatar не подключён")
    if integration.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Этот LiveAvatar подключил участник команды — отключить его может только он",
        )
    db.delete(integration)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/account", response_model=LiveAvatarAccountOut)
def liveavatar_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LiveAvatarAccountOut:
    api_key = _key(db, current_user)
    return LiveAvatarAccountOut(credits_left=_call(lambda: liveavatar.get_credits(api_key)))


@router.get("/avatars", response_model=LiveAvatarAvatarPage)
def liveavatar_avatars(
    ownership: Literal["private", "public"] = "private",
    page: int = Query(default=1, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LiveAvatarAvatarPage:
    api_key = _key(db, current_user)
    data = _call(
        lambda: liveavatar.list_avatars(api_key, public=ownership == "public", page=page)
    )
    return LiveAvatarAvatarPage.model_validate(data)
