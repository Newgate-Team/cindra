import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from passlib.context import CryptContext

from app.config import get_settings
from app.models import User

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(password, hashed_password)


def generate_url_token() -> str:
    """A high-entropy, single-use token for links emailed to users
    (password reset, email verification) -- 256 bits, not a JWT: these
    need a DB row to be revocable/single-use, which a stateless JWT
    can't do without a separate blocklist anyway."""
    return secrets.token_urlsafe(32)


def hash_url_token(token: str) -> str:
    """Only this hash is ever stored -- the raw token exists solely in
    the emailed link, same reasoning as never storing a plaintext
    password. Plain SHA-256, not argon2: the token already has 256 bits
    of entropy (unlike a human-chosen password), so a slow KDF buys
    nothing here and would only slow down legitimate lookups."""
    return hashlib.sha256(token.encode()).hexdigest()


# CIN-159: /auth/login had no brute-force protection at all -- unlimited
# password attempts against any known email. Per-account lockout (not
# per-IP/Redis-backed rate limiting) because it needs zero new infra
# and works the same on however many API instances Railway runs.
# Numbers are the common OWASP ASVS 2.2.1-style defaults, not tuned for
# this app specifically.
MAX_FAILED_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_MINUTES = 15

# A short window: the token is emailed in the clear (any plaintext
# store/proxy/inbox along the way sees it), so a shorter link lifetime
# bounds how long an intercepted link stays exploitable.
PASSWORD_RESET_TOKEN_EXPIRE_MINUTES = 30

# Longer than the reset window on purpose: verification isn't
# security-sensitive the way a password-reset link is (worst case of
# a stale link is "still unverified"), and people routinely leave a
# "confirm your email" message unread for a day or more.
EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES = 60 * 24

# A week: unlike password reset or email verification, accepting this
# is a real decision someone often needs to sit with (or just gets to
# a few days later), not something clicked within minutes of arriving.
TEAM_INVITE_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7


def is_locked_out(user: User) -> bool:
    return user.locked_until is not None and user.locked_until > datetime.now(UTC)


def record_failed_login(user: User) -> None:
    """Call on a wrong password for a known account. Does not touch
    locked_until once it's already set -- an attacker who keeps
    guessing during the lockout window must not be able to extend it
    and lock the real owner out indefinitely."""
    if is_locked_out(user):
        return
    user.failed_login_attempts += 1
    if user.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
        user.locked_until = datetime.now(UTC) + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)


def record_successful_login(user: User) -> None:
    user.failed_login_attempts = 0
    user.locked_until = None


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


YOUTUBE_OAUTH_STATE_EXPIRE_MINUTES = 10
_YOUTUBE_OAUTH_STATE_TYPE = "youtube_oauth_state"


def create_youtube_oauth_state(user_id: uuid.UUID) -> str:
    """Same purpose/CSRF reasoning as create_tiktok_oauth_state --
    binds the YouTube (Google) OAuth callback to the authenticated
    Cindra user who started the flow. A separate client/secret and
    state type from the existing Google Sign-In flow (app/google_auth.py)
    on purpose: that one is a login-only ID-token exchange with no
    refresh token; this one needs offline access + the youtube.upload
    scope to actually publish videos later, a different consent and a
    different credential the account owner registers separately.
    """
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=YOUTUBE_OAUTH_STATE_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "nonce": uuid.uuid4().hex,
        "exp": expire,
        "typ": _YOUTUBE_OAUTH_STATE_TYPE,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_youtube_oauth_state(token: str) -> uuid.UUID:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if payload.get("typ") != _YOUTUBE_OAUTH_STATE_TYPE:
        raise jwt.InvalidTokenError("not a youtube_oauth_state token")
    return uuid.UUID(payload["sub"])
