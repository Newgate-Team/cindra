import secrets

import jwt
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
    TelegramStartVerificationOut,
    TelegramStartVerificationRequest,
)
from app.security import (
    create_telegram_verification_token,
    decode_telegram_verification_token,
)
from app.social_accounts import upsert_social_account
from app.social_integrations import instagram
from app.social_integrations.errors import PermanentPublishError, TransientPublishError
from app.social_integrations.telegram import get_chat, get_chat_member, get_me

router = APIRouter(prefix="/social-accounts", tags=["social-accounts"])


@router.post(
    "/telegram/start-verification",
    response_model=TelegramStartVerificationOut,
)
def start_telegram_verification(
    payload: TelegramStartVerificationRequest,
    current_user: User = Depends(get_current_user),
) -> TelegramStartVerificationOut:
    """CIN-128: first step of connecting a Telegram channel/group --
    previously the whole flow only checked that our *bot* was a member
    of the chat, never that the *person connecting it on Cindra* had
    any real permission there. Anyone who knew a public channel's
    @username (with our bot already present in it) could hijack
    publishing rights to it. This issues a one-time code the user must
    place in the channel's description before /telegram/connect will
    accept it -- editing description requires Telegram's own "Change
    Channel Info" admin permission, so that's real proof of control.
    """
    bot_token = get_settings().telegram_bot_token
    try:
        chat = get_chat(payload.chat_id, bot_token)
    except PermanentPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Не удалось найти канал: {exc}",
        ) from exc
    except TransientPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    code = f"cindra-verify-{secrets.token_hex(4)}"
    return TelegramStartVerificationOut(
        code=code,
        verification_token=create_telegram_verification_token(payload.chat_id, code),
        chat_title=chat.get("title") or chat.get("username"),
    )


@router.post(
    "/telegram/connect", response_model=SocialAccountOut, status_code=status.HTTP_201_CREATED
)
def connect_telegram(
    payload: TelegramConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SocialAccount:
    try:
        chat_id, code = decode_telegram_verification_token(payload.verification_token)
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Код подтверждения истёк или недействителен -- начните подключение заново",
        ) from None

    bot_token = get_settings().telegram_bot_token
    try:
        chat = get_chat(chat_id, bot_token)
    except PermanentPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Не удалось подключить канал: {exc}",
        ) from exc
    except TransientPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    # The actual ownership check (CIN-128): re-fetch the description
    # fresh (not trusted from start-verification) and require the code
    # to be present right now -- only someone with Telegram's own
    # "Change Channel Info" admin permission could have put it there.
    if code not in (chat.get("description") or ""):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Код подтверждения не найден в описании канала. Убедитесь, что вы "
                "сохранили изменения в Telegram, и попробуйте ещё раз."
            ),
        )

    # get_chat succeeds for public channels even if the bot was never added
    # to them (Telegram exposes basic public-channel info to any bot) -- so
    # it alone can't tell us whether the bot can actually publish there.
    # getChatMember on the bot's own ID is what actually answers that.
    bot = get_me(bot_token)
    try:
        membership = get_chat_member(chat_id, bot["id"], bot_token)
        bot_is_member = membership["status"] not in ("left", "kicked")
    except PermanentPublishError:
        bot_is_member = False
    except TransientPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    if not bot_is_member:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Добавьте бота @{bot['username']} в канал/группу, чтобы подключить его",
        )

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
        accounts = instagram.discover_connected_accounts(long_lived_token)
    except PermanentPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Не удалось подключить аккаунт Instagram: {exc}",
        ) from exc
    except TransientPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    ig_account = accounts["instagram"]
    instagram_account = upsert_social_account(
        db,
        current_user,
        platform=SocialPlatform.instagram,
        external_account_id=ig_account["id"],
        access_token=long_lived_token,
        display_name=ig_account.get("username"),
    )

    # Same OAuth code, no extra consent screen (CIN-65): the Facebook
    # Page discovered alongside the Instagram account gets connected
    # too, using its own Page Access Token (not the user token above)
    # since that's what Facebook's Pages API requires for publishing.
    fb_page = accounts["facebook_page"]
    upsert_social_account(
        db,
        current_user,
        platform=SocialPlatform.facebook,
        external_account_id=fb_page["id"],
        access_token=fb_page["access_token"],
        display_name=fb_page.get("name"),
    )

    return instagram_account


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
