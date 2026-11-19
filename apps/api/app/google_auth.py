import httpx

from app.config import get_settings

# Google's official ID-token validation endpoint: it checks the
# signature and expiry itself and returns the claims. Chosen over the
# google-auth library to avoid a new dependency for one call -- the
# trade-off is one extra HTTPS round-trip per Google login, which is
# fine at login frequency. POST body (not query param) so the token
# never lands in URL/access logs.
_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"

_VALID_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class GoogleAuthError(Exception):
    """The Google ID token could not be verified (invalid, expired,
    wrong audience, unverified email, or Google unreachable)."""


def verify_google_id_token(id_token: str, client: httpx.Client | None = None) -> dict:
    """Validate a Google ID token and return its claims.

    `client` is only for tests to inject an httpx.MockTransport --
    production always uses the default real client.
    """
    settings = get_settings()
    request_kwargs = {"data": {"id_token": id_token}, "timeout": 10.0}
    try:
        response = (
            client.post(_TOKENINFO_URL, **request_kwargs)
            if client is not None
            else httpx.post(_TOKENINFO_URL, **request_kwargs)
        )
    except httpx.TransportError as exc:
        raise GoogleAuthError(
            "Не удалось проверить токен Google, попробуйте ещё раз"
        ) from exc
    if response.status_code != 200:
        raise GoogleAuthError("Недействительный или просроченный токен Google")
    claims = response.json()
    if claims.get("iss") not in _VALID_ISSUERS:
        raise GoogleAuthError("Недействительный или просроченный токен Google")
    if claims.get("aud") != settings.google_client_id:
        raise GoogleAuthError("Токен Google выдан для другого приложения")
    # tokeninfo returns booleans as strings ("true"/"false")
    if claims.get("email_verified") != "true":
        raise GoogleAuthError("Email в Google-аккаунте не подтверждён")
    if not claims.get("email"):
        raise GoogleAuthError("Google не вернул email")
    return claims
