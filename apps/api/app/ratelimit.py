"""Request rate limits for the endpoints anyone on the internet can call.

Login already has a per-account lockout (CIN-159). What was open: the
endpoints that cost us something per call (registration, and the two
that send email) and the ones a script can hammer without an account.
Fixed windows in Redis -- the same Redis Celery uses -- so the limit
holds across API replicas.

Fails OPEN: if Redis is unreachable the request goes through and a
warning is logged. A cache outage must not lock every user out of
signing in; the limits are abuse protection, not a security boundary
(login lockout and password hashing are).
"""

import hashlib
import logging
import time
from functools import lru_cache

import redis
from fastapi import HTTPException, Request, status

from app.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def _client() -> redis.Redis:
    # Short timeouts: a hung Redis must not hang sign-in with it.
    return redis.Redis.from_url(
        get_settings().redis_url, socket_connect_timeout=0.5, socket_timeout=0.5
    )


def client_ip(request: Request) -> str:
    """The caller's address, honouring the reverse proxy only as configured.

    Behind Railway/Vercel every request arrives from the proxy, so
    request.client is the same address for everyone and the limit would
    throttle all users at once. TRUSTED_PROXY_HOPS says how many proxies
    append to X-Forwarded-For; the entry that many places from the END is
    the address our outermost trusted proxy actually saw. Entries further
    left are whatever the client chose to send, so they are never used.
    0 (the default) ignores the header entirely.
    """
    hops = get_settings().trusted_proxy_hops
    if hops > 0:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return request.client.host if request.client else "unknown"


def hit(scope: str, key: str, limit: int, window_seconds: int) -> None:
    """Count one call; raise 429 once `limit` calls happened in the window."""
    if not get_settings().rate_limit_enabled:
        return
    bucket = int(time.time() // window_seconds)
    redis_key = f"rl:{scope}:{hashlib.sha256(key.encode()).hexdigest()[:32]}:{bucket}"
    try:
        pipe = _client().pipeline()
        pipe.incr(redis_key)
        pipe.expire(redis_key, window_seconds + 5)
        count = int(pipe.execute()[0])
    except redis.RedisError:
        logger.warning("rate limit skipped: Redis unreachable", exc_info=True)
        return
    if count > limit:
        retry_after = window_seconds - int(time.time() % window_seconds)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много запросов — попробуйте чуть позже",
            headers={"Retry-After": str(retry_after)},
        )


def limit_by_ip(scope: str, limit: int, window_seconds: int):
    """A FastAPI dependency: `limit` calls per `window_seconds` per address."""

    def dependency(request: Request) -> None:
        hit(scope, client_ip(request), limit, window_seconds)

    return dependency
