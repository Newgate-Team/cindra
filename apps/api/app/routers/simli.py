"""Simli (cheaper real-time avatars) on «Инструменты»: connect a key and
pick the face that hosts a live stream."""

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
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
    LiveAvatarAvatarPage,
    LiveAvatarConnectRequest,
    SimliAccountOut,
)
from app.tool_integrations import simli

router = APIRouter(prefix="/integrations/simli", tags=["integrations"])

_STORED_KEY_REJECTED = "Ключ Simli недействителен или отозван — подключите Simli заново"


def _call[T](fn: Callable[[], T], auth_message: str = _STORED_KEY_REJECTED) -> T:
    try:
        return fn()
    except simli.SimliAuthError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=auth_message) from None
    except simli.SimliTransientError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except simli.SimliError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


def _key(db: Session, user: User) -> str:
    integration = find_integration(db, user, IntegrationProvider.simli)
    if integration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simli не подключён")
    return get_secret(integration)


@router.put("", response_model=IntegrationOut)
def connect_simli(
    payload: LiveAvatarConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IntegrationOut:
    _call(
        lambda: simli.active_sessions(payload.api_key),
        auth_message="Simli не принял этот ключ — скопируйте его из app.simli.com → API Keys",
    )
    integration = upsert_integration(
        db, current_user, IntegrationProvider.simli, payload.api_key, "Simli"
    )
    return integration_out(integration, current_user)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_simli(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    integration = find_integration(db, current_user, IntegrationProvider.simli)
    if integration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simli не подключён")
    if integration.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Этот Simli подключил участник команды — отключить его может только он",
        )
    db.delete(integration)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/account", response_model=SimliAccountOut)
def simli_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SimliAccountOut:
    api_key = _key(db, current_user)
    return SimliAccountOut(active_sessions=_call(lambda: simli.active_sessions(api_key)))


@router.get("/avatars", response_model=LiveAvatarAvatarPage)
def simli_faces(
    ownership: Literal["private", "public"] = "private",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LiveAvatarAvatarPage:
    api_key = _key(db, current_user)
    data = _call(lambda: simli.list_faces(api_key, public=ownership == "public"))
    return LiveAvatarAvatarPage.model_validate(data)
