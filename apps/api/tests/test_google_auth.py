import httpx
import pytest

from app.config import get_settings
from app.google_auth import GoogleAuthError, verify_google_id_token

_CLIENT_ID = "test-client-id.apps.googleusercontent.com"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _claims(**overrides):
    claims = {
        "iss": "https://accounts.google.com",
        "aud": _CLIENT_ID,
        "email": "google-user@gmail.com",
        "email_verified": "true",
        "sub": "1234567890",
    }
    claims.update(overrides)
    return claims


@pytest.fixture(autouse=True)
def _configured_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "google_client_id", _CLIENT_ID)


def test_valid_token_returns_claims() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert b"id_token=valid-token" in request.content
        return httpx.Response(200, json=_claims())

    claims = verify_google_id_token("valid-token", client=_client(handler))
    assert claims["email"] == "google-user@gmail.com"


def test_token_is_sent_in_body_not_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "id_token" not in str(request.url)
        return httpx.Response(200, json=_claims())

    verify_google_id_token("valid-token", client=_client(handler))


def test_non_200_from_google_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_token"})

    with pytest.raises(GoogleAuthError):
        verify_google_id_token("expired-token", client=_client(handler))


def test_wrong_audience_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_claims(aud="someone-elses-app"))

    with pytest.raises(GoogleAuthError):
        verify_google_id_token("foreign-token", client=_client(handler))


def test_wrong_issuer_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_claims(iss="https://evil.example"))

    with pytest.raises(GoogleAuthError):
        verify_google_id_token("forged-token", client=_client(handler))


def test_unverified_email_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_claims(email_verified="false"))

    with pytest.raises(GoogleAuthError):
        verify_google_id_token("unverified-token", client=_client(handler))


def test_missing_email_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        claims = _claims()
        del claims["email"]
        return httpx.Response(200, json=claims)

    with pytest.raises(GoogleAuthError):
        verify_google_id_token("no-email-token", client=_client(handler))


def test_network_error_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(GoogleAuthError):
        verify_google_id_token("any-token", client=_client(handler))
