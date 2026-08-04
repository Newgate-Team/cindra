from typing import Any

import httpx

from app.config import get_settings

_SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
_LIVE_BASE_URL = "https://api-m.paypal.com"


def _base_url() -> str:
    return _LIVE_BASE_URL if get_settings().paypal_mode == "live" else _SANDBOX_BASE_URL


def get_access_token(client: httpx.Client | None = None) -> str:
    """OAuth2 client_credentials grant -- verified live against
    api-m.sandbox.paypal.com with real sandbox credentials (see
    CIN-85): returns a real Bearer token with the expected scopes
    (billing-agreements, subscriptions, applications/webhooks).
    """
    settings = get_settings()
    url = f"{_base_url()}/v1/oauth2/token"
    post = client.post if client is not None else httpx.post
    response = post(
        url,
        auth=(settings.paypal_client_id, settings.paypal_client_secret),
        data={"grant_type": "client_credentials"},
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def get_subscription(subscription_id: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """GET /v1/billing/subscriptions/{id} -- used to verify a
    frontend-supplied subscription_id is real before trusting it
    (never trust client-supplied data blindly). Response includes
    `status`, `plan_id`, `custom_id` -- fields confirmed against
    PayPal's official OpenAPI spec (subscription_request_post /
    subscription schemas in paypal/paypal-rest-api-specifications,
    see CIN-85), not guessed.
    """
    token = get_access_token(client)
    url = f"{_base_url()}/v1/billing/subscriptions/{subscription_id}"
    get = client.get if client is not None else httpx.get
    response = get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15.0)
    response.raise_for_status()
    return response.json()
