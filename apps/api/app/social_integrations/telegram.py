from typing import Any

import httpx

from app.models import SocialAccount
from app.social_accounts import get_access_token
from app.social_integrations.errors import PermanentPublishError, TransientPublishError

_TELEGRAM_API_BASE = "https://api.telegram.org"

# 429 (Telegram's own flood control) and 5xx are worth retrying;
# anything else (bad token, bot not a channel admin, unknown chat) is
# a permanent failure that won't fix itself.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _handle_response(response: httpx.Response) -> dict[str, Any]:
    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientPublishError(
            f"Telegram API {response.status_code}: {response.text[:500]}"
        )
    body = response.json()
    if not body.get("ok", False):
        raise PermanentPublishError(
            f"Telegram API error: {body.get('description', response.text[:500])}"
        )
    return body["result"]


def get_chat(
    chat_id: str, bot_token: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Verify the bot can see `chat_id` and fetch its info (used to connect a channel)."""
    url = f"{_TELEGRAM_API_BASE}/bot{bot_token}/getChat"
    response = (
        client.get(url, params={"chat_id": chat_id}, timeout=15.0)
        if client is not None
        else httpx.get(url, params={"chat_id": chat_id}, timeout=15.0)
    )
    return _handle_response(response)


def send_message(
    chat_id: str, text: str, bot_token: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Publish `text` to `chat_id`. Real POST to api.telegram.org/bot<token>/sendMessage."""
    url = f"{_TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    json_body = {"chat_id": chat_id, "text": text}
    response = (
        client.post(url, json=json_body, timeout=15.0)
        if client is not None
        else httpx.post(url, json=json_body, timeout=15.0)
    )
    return _handle_response(response)


def publish(account: SocialAccount, text: str) -> dict[str, Any]:
    """Registered in app.scheduler.registry as the telegram publisher."""
    return send_message(account.external_account_id, text, get_access_token(account))
