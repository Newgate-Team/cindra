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
