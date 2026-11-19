import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

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
    LinkedInConnectRequest,
    LinkedInOAuthStartOut,
    MetaOAuthStartOut,
    RedditConnectRequest,
    RedditOAuthStartOut,
    SocialAccountOut,
    TelegramConnectRequest,
    TelegramStartVerificationOut,
    TelegramStartVerificationRequest,
    TikTokConnectRequest,
    TikTokCreatorInfoOut,
    TikTokOAuthStartOut,
    TikTokPublishStatusOut,
    TwitterConnectRequest,
    TwitterOAuthStartOut,
    YouTubeConnectRequest,
    YouTubeOAuthStartOut,
)
from app.security import (
    create_linkedin_oauth_state,
    create_meta_oauth_state,
    create_reddit_oauth_state,
    create_telegram_verification_token,
    create_tiktok_oauth_state,
    create_twitter_oauth_state,
    create_youtube_oauth_state,
    decode_linkedin_oauth_state,
    decode_meta_oauth_state,
    decode_reddit_oauth_state,
    decode_telegram_verification_token,
    decode_tiktok_oauth_state,
    decode_twitter_oauth_state,
    decode_youtube_oauth_state,
)
from app.social_accounts import upsert_social_account
from app.social_integrations import (
    instagram,
    linkedin,
    reddit,
    tiktok,
    twitter,
    youtube,
)
from app.social_integrations.errors import PermanentPublishError, TransientPublishError
from app.social_integrations.telegram import get_chat, get_chat_member, get_me
from app.teams import visible_user_ids

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


@router.post("/instagram/start", response_model=MetaOAuthStartOut)
def start_instagram_oauth(
    current_user: User = Depends(get_current_user),
) -> MetaOAuthStartOut:
    """Issues the CSRF state for the Meta OAuth dialog (CIN-154).

    Unlike TikTok's /tiktok/start, this doesn't also build the full
    authorization URL: NEXT_PUBLIC_META_APP_ID and
    NEXT_PUBLIC_META_REDIRECT_URI are already public (the frontend
    builds https://www.facebook.com/.../dialog/oauth itself), so the
    only thing the backend needs to hand over is the signed state.
    """
    return MetaOAuthStartOut(state=create_meta_oauth_state(current_user.id))


@router.post(
    "/instagram/connect", response_model=SocialAccountOut, status_code=status.HTTP_201_CREATED
)
def connect_instagram(
    payload: InstagramConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SocialAccount:
    # CIN-154: reject before spending a Meta API round-trip on a code
    # that didn't originate from this user's own OAuth start.
    try:
        state_user_id = decode_meta_oauth_state(payload.state)
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state истёк или недействителен — начните подключение заново",
        ) from None
    if state_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth был начат другим пользователем",
        )

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


@router.post("/tiktok/start", response_model=TikTokOAuthStartOut)
def start_tiktok_oauth(
    current_user: User = Depends(get_current_user),
) -> TikTokOAuthStartOut:
    settings = get_settings()
    if not settings.tiktok_client_key or not settings.tiktok_client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TikTok App ещё не настроено на сервере",
        )
    state_token = create_tiktok_oauth_state(current_user.id)
    query = urlencode(
        {
            "client_key": settings.tiktok_client_key,
            "response_type": "code",
            "scope": "user.info.basic,video.upload,video.publish",
            "redirect_uri": settings.tiktok_redirect_uri,
            "state": state_token,
        }
    )
    return TikTokOAuthStartOut(
        authorization_url=f"https://www.tiktok.com/v2/auth/authorize/?{query}"
    )


@router.post(
    "/tiktok/connect", response_model=SocialAccountOut, status_code=status.HTTP_201_CREATED
)
def connect_tiktok(
    payload: TikTokConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SocialAccount:
    try:
        state_user_id = decode_tiktok_oauth_state(payload.state)
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TikTok OAuth state истёк или недействителен — начните подключение заново",
        ) from None
    if state_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TikTok OAuth был начат другим пользователем",
        )

    settings = get_settings()
    try:
        token = tiktok.exchange_code_for_token(
            payload.code,
            settings.tiktok_client_key,
            settings.tiktok_client_secret,
            settings.tiktok_redirect_uri,
        )
        granted_scopes = {value.strip() for value in token.get("scope", "").split(",")}
        if "video.publish" not in granted_scopes:
            raise PermanentPublishError(
                "разрешение video.publish не выдано — повторите вход и подтвердите публикацию"
            )
        creator = tiktok.query_creator_info(token["access_token"])
        account = upsert_social_account(
            db,
            current_user,
            platform=SocialPlatform.tiktok,
            external_account_id=token["open_id"],
            access_token=token["access_token"],
            refresh_token=token["refresh_token"],
            token_expires_at=datetime.now(UTC)
            + timedelta(seconds=int(token["expires_in"])),
            display_name=creator.get("creator_nickname")
            or creator.get("creator_username"),
        )
    except (KeyError, ValueError, PermanentPublishError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Не удалось подключить TikTok: {exc}",
        ) from exc
    except TransientPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return account


@router.post("/youtube/start", response_model=YouTubeOAuthStartOut)
def start_youtube_oauth(
    current_user: User = Depends(get_current_user),
) -> YouTubeOAuthStartOut:
    settings = get_settings()
    if not settings.youtube_client_id or not settings.youtube_client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="YouTube ещё не настроен на сервере",
        )
    state_token = create_youtube_oauth_state(current_user.id)
    query = urlencode(
        {
            "client_id": settings.youtube_client_id,
            "redirect_uri": settings.youtube_redirect_uri,
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/youtube.upload",
            # offline + consent: without both, Google only issues a
            # refresh_token on the very first consent a user ever
            # grants this client -- a reconnect after revoking access
            # would otherwise silently come back with no refresh_token
            # at all (ensure_fresh_access_token would then have nothing
            # to refresh with once the short-lived access token expired).
            "access_type": "offline",
            "prompt": "consent",
            "state": state_token,
        }
    )
    return YouTubeOAuthStartOut(
        authorization_url=f"https://accounts.google.com/o/oauth2/v2/auth?{query}"
    )


@router.post(
    "/youtube/connect", response_model=SocialAccountOut, status_code=status.HTTP_201_CREATED
)
def connect_youtube(
    payload: YouTubeConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SocialAccount:
    try:
        state_user_id = decode_youtube_oauth_state(payload.state)
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="YouTube OAuth state истёк или недействителен — начните подключение заново",
        ) from None
    if state_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="YouTube OAuth был начат другим пользователем",
        )

    settings = get_settings()
    try:
        token = youtube.exchange_code_for_token(
            payload.code,
            settings.youtube_client_id,
            settings.youtube_client_secret,
            settings.youtube_redirect_uri,
        )
        channel = youtube.get_channel_info(token["access_token"])
        account = upsert_social_account(
            db,
            current_user,
            platform=SocialPlatform.youtube,
            external_account_id=channel["id"],
            access_token=token["access_token"],
            refresh_token=token.get("refresh_token"),
            token_expires_at=datetime.now(UTC) + timedelta(seconds=int(token["expires_in"])),
            display_name=channel.get("snippet", {}).get("title"),
        )
    except (KeyError, ValueError, PermanentPublishError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Не удалось подключить YouTube: {exc}",
        ) from exc
    except TransientPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return account


@router.post("/linkedin/start", response_model=LinkedInOAuthStartOut)
def start_linkedin_oauth(
    current_user: User = Depends(get_current_user),
) -> LinkedInOAuthStartOut:
    settings = get_settings()
    if not settings.linkedin_client_id or not settings.linkedin_client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LinkedIn ещё не настроен на сервере",
        )
    state_token = create_linkedin_oauth_state(current_user.id)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.linkedin_client_id,
            "redirect_uri": settings.linkedin_redirect_uri,
            # openid+profile: identify the member (OIDC userinfo) to get
            # their own URN. w_member_social: "Share on LinkedIn" --
            # create/manage posts as that member. Both auto-approved
            # products, no partner review needed (unlike LinkedIn's
            # Marketing/organization-posting APIs).
            "scope": "openid profile w_member_social",
            "state": state_token,
        }
    )
    return LinkedInOAuthStartOut(
        authorization_url=f"https://www.linkedin.com/oauth/v2/authorization?{query}"
    )


@router.post(
    "/linkedin/connect", response_model=SocialAccountOut, status_code=status.HTTP_201_CREATED
)
def connect_linkedin(
    payload: LinkedInConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SocialAccount:
    try:
        state_user_id = decode_linkedin_oauth_state(payload.state)
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LinkedIn OAuth state истёк или недействителен — начните подключение заново",
        ) from None
    if state_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LinkedIn OAuth был начат другим пользователем",
        )

    settings = get_settings()
    try:
        token = linkedin.exchange_code_for_token(
            payload.code,
            settings.linkedin_client_id,
            settings.linkedin_client_secret,
            settings.linkedin_redirect_uri,
        )
        member = linkedin.get_member_info(token["access_token"])
        account = upsert_social_account(
            db,
            current_user,
            platform=SocialPlatform.linkedin,
            external_account_id=member["sub"],
            access_token=token["access_token"],
            refresh_token=token.get("refresh_token"),
            token_expires_at=datetime.now(UTC) + timedelta(seconds=int(token["expires_in"])),
            display_name=member.get("name"),
        )
    except (KeyError, ValueError, PermanentPublishError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Не удалось подключить LinkedIn: {exc}",
        ) from exc
    except TransientPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return account


@router.post("/reddit/start", response_model=RedditOAuthStartOut)
def start_reddit_oauth(
    current_user: User = Depends(get_current_user),
) -> RedditOAuthStartOut:
    settings = get_settings()
    if not settings.reddit_client_id or not settings.reddit_client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reddit ещё не настроен на сервере",
        )
    state_token = create_reddit_oauth_state(current_user.id)
    query = urlencode(
        {
            "client_id": settings.reddit_client_id,
            "response_type": "code",
            "state": state_token,
            "redirect_uri": settings.reddit_redirect_uri,
            # duration=permanent: without it Reddit issues a 1-hour
            # access token and NO refresh token at all (a "temporary"
            # grant meant for short read-only sessions) -- publishing
            # later from a background task needs a refresh token.
            "duration": "permanent",
            "scope": "identity submit",
        }
    )
    return RedditOAuthStartOut(
        authorization_url=f"https://www.reddit.com/api/v1/authorize?{query}"
    )


@router.post(
    "/reddit/connect", response_model=SocialAccountOut, status_code=status.HTTP_201_CREATED
)
def connect_reddit(
    payload: RedditConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SocialAccount:
    try:
        state_user_id = decode_reddit_oauth_state(payload.state)
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reddit OAuth state истёк или недействителен — начните подключение заново",
        ) from None
    if state_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reddit OAuth был начат другим пользователем",
        )

    settings = get_settings()
    try:
        token = reddit.exchange_code_for_token(
            payload.code,
            settings.reddit_client_id,
            settings.reddit_client_secret,
            settings.reddit_redirect_uri,
            settings.reddit_user_agent,
        )
        identity = reddit.get_identity(token["access_token"], settings.reddit_user_agent)
        account = upsert_social_account(
            db,
            current_user,
            platform=SocialPlatform.reddit,
            external_account_id=identity["name"],
            access_token=token["access_token"],
            refresh_token=token.get("refresh_token"),
            token_expires_at=datetime.now(UTC) + timedelta(seconds=int(token["expires_in"])),
            display_name=identity.get("name"),
        )
    except (KeyError, ValueError, PermanentPublishError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Не удалось подключить Reddit: {exc}",
        ) from exc
    except TransientPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return account


@router.post("/twitter/start", response_model=TwitterOAuthStartOut)
def start_twitter_oauth(
    current_user: User = Depends(get_current_user),
) -> TwitterOAuthStartOut:
    settings = get_settings()
    if not settings.twitter_client_id or not settings.twitter_client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="X (Twitter) ещё не настроен на сервере",
        )
    state_token, code_challenge = create_twitter_oauth_state(current_user.id)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.twitter_client_id,
            "redirect_uri": settings.twitter_redirect_uri,
            # offline.access: without it X issues no refresh token at
            # all, same reasoning as YouTube's access_type=offline.
            "scope": "tweet.read tweet.write users.read offline.access",
            "state": state_token,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return TwitterOAuthStartOut(
        authorization_url=f"https://twitter.com/i/oauth2/authorize?{query}"
    )


@router.post(
    "/twitter/connect", response_model=SocialAccountOut, status_code=status.HTTP_201_CREATED
)
def connect_twitter(
    payload: TwitterConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SocialAccount:
    try:
        state_user_id, code_verifier = decode_twitter_oauth_state(payload.state)
    except (jwt.InvalidTokenError, ValueError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X OAuth state истёк или недействителен — начните подключение заново",
        ) from None
    if state_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X OAuth был начат другим пользователем",
        )

    settings = get_settings()
    try:
        token = twitter.exchange_code_for_token(
            payload.code,
            code_verifier,
            settings.twitter_client_id,
            settings.twitter_client_secret,
            settings.twitter_redirect_uri,
        )
        me = twitter.get_me(token["access_token"])
        account = upsert_social_account(
            db,
            current_user,
            platform=SocialPlatform.twitter,
            external_account_id=me["id"],
            access_token=token["access_token"],
            refresh_token=token.get("refresh_token"),
            token_expires_at=datetime.now(UTC) + timedelta(seconds=int(token["expires_in"])),
            display_name=me.get("username"),
        )
    except (KeyError, ValueError, PermanentPublishError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Не удалось подключить X: {exc}",
        ) from exc
    except TransientPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return account


def _visible_tiktok_account(db: Session, account_id: str, user: User) -> SocialAccount:
    account = db.get(SocialAccount, account_id)
    if (
        account is None
        or account.user_id not in visible_user_ids(db, user)
        or account.platform != SocialPlatform.tiktok
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="TikTok аккаунт не найден")
    return account


@router.get("/{account_id}/tiktok/creator-info", response_model=TikTokCreatorInfoOut)
def get_tiktok_creator_info(
    account_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TikTokCreatorInfoOut:
    account = _visible_tiktok_account(db, account_id, current_user)
    try:
        access_token = tiktok.ensure_fresh_access_token(account)
        creator = tiktok.query_creator_info(access_token)
        db.commit()
        return TikTokCreatorInfoOut.model_validate(creator)
    except (ValueError, PermanentPublishError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TransientPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get("/{account_id}/tiktok/publish-status", response_model=TikTokPublishStatusOut)
def get_tiktok_publish_status(
    account_id: str,
    publish_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TikTokPublishStatusOut:
    account = _visible_tiktok_account(db, account_id, current_user)
    try:
        access_token = tiktok.ensure_fresh_access_token(account)
        result = tiktok.fetch_publish_status(access_token, publish_id)
        db.commit()
        return TikTokPublishStatusOut.model_validate(result)
    except (ValueError, PermanentPublishError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TransientPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get("", response_model=list[SocialAccountOut])
def list_social_accounts(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[SocialAccount]:
    # Roadmap item 5: team-visible, not just the caller's own rows --
    # a shared channel list is the actual point of a team.
    return list(
        db.scalars(
            select(SocialAccount).where(
                SocialAccount.user_id.in_(visible_user_ids(db, current_user))
            )
        )
    )


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_social_account(
    account_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    # Any team member can manage a shared account, not just whoever
    # originally connected it -- same reasoning as the list above.
    result = db.execute(
        delete(SocialAccount).where(
            SocialAccount.id == account_id,
            SocialAccount.user_id.in_(visible_user_ids(db, current_user)),
        )
    )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Аккаунт не найден")
