from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.integrations import find_integration, get_secret, upsert_integration
from app.models import Integration, IntegrationProvider, User
from app.schemas import (
    HeyGenAccountOut,
    HeyGenAvatarPage,
    HeyGenConnectRequest,
    HeyGenVoicePage,
    IntegrationOut,
)
from app.tool_integrations import heygen

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _out(integration: Integration, user: User) -> IntegrationOut:
    return IntegrationOut(
        provider=integration.provider,
        account_label=integration.account_label,
        created_at=integration.created_at,
        is_own=integration.user_id == user.id,
    )


_STORED_KEY_REJECTED = "Ключ HeyGen недействителен или отозван — подключите HeyGen заново"


def _call_heygen[T](fn: Callable[[], T], auth_message: str = _STORED_KEY_REJECTED) -> T:
    try:
        return fn()
    except heygen.HeyGenAuthError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=auth_message
        ) from None
    except heygen.HeyGenTransientError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except heygen.HeyGenError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


def _heygen_key(db: Session, user: User) -> str:
    integration = find_integration(db, user, IntegrationProvider.heygen)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="HeyGen не подключён"
        )
    return get_secret(integration)


@router.get("", response_model=list[IntegrationOut])
def list_integrations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[IntegrationOut]:
    found = (find_integration(db, current_user, provider) for provider in IntegrationProvider)
    return [_out(integration, current_user) for integration in found if integration]


@router.put("/heygen", response_model=IntegrationOut)
def connect_heygen(
    payload: HeyGenConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IntegrationOut:
    # Validated against HeyGen before it's stored, so a typo surfaces
    # here, on the connect form, instead of as a failed video later.
    account = _call_heygen(
        lambda: heygen.get_current_user(payload.api_key),
        auth_message="HeyGen не принял этот ключ — проверьте, что он скопирован целиком "
        "(HeyGen → Settings → API)",
    )
    integration = upsert_integration(
        db,
        current_user,
        IntegrationProvider.heygen,
        payload.api_key,
        heygen.account_label(account),
    )
    return _out(integration, current_user)


@router.delete("/heygen", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_heygen(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    integration = find_integration(db, current_user, IntegrationProvider.heygen)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="HeyGen не подключён"
        )
    db.delete(integration)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/heygen/account", response_model=HeyGenAccountOut)
def heygen_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HeyGenAccountOut:
    api_key = _heygen_key(db, current_user)
    account = _call_heygen(lambda: heygen.get_current_user(api_key))
    balance, currency = heygen.wallet_balance(account)
    return HeyGenAccountOut(
        account_label=heygen.account_label(account),
        billing_type=account.get("billing_type"),
        remaining_balance=balance,
        currency=currency,
    )


@router.get("/heygen/avatars", response_model=HeyGenAvatarPage)
def heygen_avatars(
    token: str | None = Query(default=None, max_length=512),
    ownership: Literal["public", "private"] | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HeyGenAvatarPage:
    api_key = _heygen_key(db, current_user)
    page = _call_heygen(
        lambda: heygen.list_avatar_looks(api_key, token=token, ownership=ownership)
    )
    return HeyGenAvatarPage.model_validate(page)


@router.get("/heygen/voices", response_model=HeyGenVoicePage)
def heygen_voices(
    token: str | None = Query(default=None, max_length=512),
    language: str | None = Query(default=None, max_length=64),
    gender: Literal["male", "female"] | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HeyGenVoicePage:
    api_key = _heygen_key(db, current_user)
    page = _call_heygen(
        lambda: heygen.list_voices(api_key, token=token, language=language, gender=gender)
    )
    return HeyGenVoicePage.model_validate(page)
