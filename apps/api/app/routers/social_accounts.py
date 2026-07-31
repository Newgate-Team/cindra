from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import SocialAccount, SocialPlatform, User
from app.schemas import (
    InstagramConnectRequest,
    SocialAccountOut,
    TelegramConnectRequest,
)
from app.social_accounts import upsert_social_account
from app.social_integrations import instagram
from app.social_integrations.errors import PermanentPublishError, TransientPublishError
from app.social_integrations.telegram import get_chat

router = APIRouter(prefix="/social-accounts", tags=["social-accounts"])


@router.post(
    "/telegram/connect", response_model=SocialAccountOut, status_code=status.HTTP_201_CREATED
)
def connect_telegram(
    payload: TelegramConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SocialAccount:
    bot_token = get_settings().telegram_bot_token
    try:
        chat = get_chat(payload.chat_id, bot_token)
    except PermanentPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Не удалось подключить канал: {exc}",
        ) from exc
    except TransientPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    return upsert_social_account(
        db,
        current_user,
        platform=SocialPlatform.telegram,
        external_account_id=str(chat["id"]),
        access_token=bot_token,
        display_name=chat.get("title") or chat.get("username"),
    )


@router.post(
    "/instagram/connect", response_model=SocialAccountOut, status_code=status.HTTP_201_CREATED
)
def connect_instagram(
    payload: InstagramConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SocialAccount:
    settings = get_settings()
    try:
        short_lived_token = instagram.exchange_code_for_token(
            payload.code,
            settings.meta_redirect_uri,
            settings.meta_app_id,
            settings.meta_app_secret,
        )
        long_lived_token = instagram.get_long_lived_token(
            short_lived_token, settings.meta_app_id, settings.meta_app_secret
        )
        ig_account = instagram.discover_instagram_business_account(long_lived_token)
    except PermanentPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Не удалось подключить аккаунт Instagram: {exc}",
        ) from exc
    except TransientPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    return upsert_social_account(
        db,
        current_user,
        platform=SocialPlatform.instagram,
        external_account_id=ig_account["id"],
        access_token=long_lived_token,
        display_name=ig_account.get("username"),
    )


@router.get("", response_model=list[SocialAccountOut])
def list_social_accounts(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[SocialAccount]:
    return list(
        db.scalars(select(SocialAccount).where(SocialAccount.user_id == current_user.id))
    )


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_social_account(
    account_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    result = db.execute(
        delete(SocialAccount).where(
            SocialAccount.id == account_id, SocialAccount.user_id == current_user.id
        )
    )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Аккаунт не найден")
