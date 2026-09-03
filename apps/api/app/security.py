import uuid
from datetime import UTC, datetime, timedelta

import jwt
from passlib.context import CryptContext

from app.config import get_settings

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(password, hashed_password)


def create_access_token(user_id: uuid.UUID) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> uuid.UUID:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    # CIN-157: every special-purpose token below (oauth state, telegram
    # verification) is signed with this same jwt_secret and also carries
    # `sub` -- without this check, any of them decodes here as a fully
    # valid access token for that user. That's a real escalation for the
    # oauth state tokens specifically, since they're embedded in a URL
    # handed to a third party (TikTok/Meta) and round-tripped through
    # browser history/Referer, unlike an access token which never leaves
    # our own Authorization header. A real access token never sets
    # `typ`, so any token carrying one is, by construction, not one.
    if payload.get("typ") is not None:
        raise jwt.InvalidTokenError("not an access token")
    return uuid.UUID(payload["sub"])


TELEGRAM_VERIFICATION_EXPIRE_MINUTES = 10
_TELEGRAM_VERIFICATION_TYPE = "telegram_verification"


def create_telegram_verification_token(chat_id: str, code: str) -> str:
    """Binds a one-time ownership-verification `code` to the specific
    `chat_id` it was issued for (CIN-128) -- signed so /telegram/connect
    can trust the pair came from our own /telegram/start-verification
    call rather than being supplied directly by the client."""
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=TELEGRAM_VERIFICATION_EXPIRE_MINUTES)
    payload = {"chat_id": chat_id, "code": code, "exp": expire, "typ": _TELEGRAM_VERIFICATION_TYPE}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_telegram_verification_token(token: str) -> tuple[str, str]:
    """Returns (chat_id, code). Raises jwt.InvalidTokenError (expired,
    bad signature, or wrong token type) if the token isn't a valid,
    current telegram_verification token."""
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if payload.get("typ") != _TELEGRAM_VERIFICATION_TYPE:
        raise jwt.InvalidTokenError("not a telegram_verification token")
    return payload["chat_id"], payload["code"]


TIKTOK_OAUTH_STATE_EXPIRE_MINUTES = 10
_TIKTOK_OAUTH_STATE_TYPE = "tiktok_oauth_state"


def create_tiktok_oauth_state(user_id: uuid.UUID) -> str:
    """Short-lived signed state binds the TikTok callback to the
    authenticated Cindra user who started the OAuth flow."""
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=TIKTOK_OAUTH_STATE_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "nonce": uuid.uuid4().hex,
        "exp": expire,
        "typ": _TIKTOK_OAUTH_STATE_TYPE,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_tiktok_oauth_state(token: str) -> uuid.UUID:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if payload.get("typ") != _TIKTOK_OAUTH_STATE_TYPE:
        raise jwt.InvalidTokenError("not a tiktok_oauth_state token")
    return uuid.UUID(payload["sub"])


META_OAUTH_STATE_EXPIRE_MINUTES = 10
_META_OAUTH_STATE_TYPE = "meta_oauth_state"


def create_meta_oauth_state(user_id: uuid.UUID) -> str:
    """Same purpose as create_tiktok_oauth_state (CIN-154): binds the
    Meta (Instagram/Facebook) OAuth callback to the authenticated
    Cindra user who started the flow. Without this, /instagram/connect
    had no way to tell "this code came from whoever's browser is
    calling us right now" apart from "this code came from the user who
    actually completed Meta's consent screen" -- letting an attacker
    who completes consent as themselves hand their own `code` to a
    victim and get their Instagram/Facebook linked to the victim's
    Cindra account (OAuth login CSRF, RFC 6749 section 10.12).
    """
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=META_OAUTH_STATE_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "nonce": uuid.uuid4().hex,
        "exp": expire,
        "typ": _META_OAUTH_STATE_TYPE,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_meta_oauth_state(token: str) -> uuid.UUID:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if payload.get("typ") != _META_OAUTH_STATE_TYPE:
        raise jwt.InvalidTokenError("not a meta_oauth_state token")
    return uuid.UUID(payload["sub"])
