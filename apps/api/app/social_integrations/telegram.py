from typing import Any

import httpx

from app.models import Post, SocialAccount
from app.social_accounts import get_access_token
from app.social_integrations.errors import PermanentPublishError, TransientPublishError
from app.social_integrations.text_formatting import to_telegram_markdown_v2

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


def get_me(bot_token: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """Fetch the bot's own identity (id, username) -- used to check its membership
    in a chat before connecting it (see get_chat_member)."""
    url = f"{_TELEGRAM_API_BASE}/bot{bot_token}/getMe"
    response = (
        client.get(url, timeout=15.0) if client is not None else httpx.get(url, timeout=15.0)
    )
    return _handle_response(response)


def get_chat_member(
    chat_id: str, user_id: int, bot_token: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Look up the bot's own membership status in `chat_id`. Unlike get_chat
    (which succeeds for public channels even if the bot was never added),
    this is what actually tells us whether the bot can publish there."""
    url = f"{_TELEGRAM_API_BASE}/bot{bot_token}/getChatMember"
    params = {"chat_id": chat_id, "user_id": user_id}
    response = (
        client.get(url, params=params, timeout=15.0)
        if client is not None
        else httpx.get(url, params=params, timeout=15.0)
    )
    return _handle_response(response)


def send_message(
    chat_id: str, text: str, bot_token: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Publish `text` to `chat_id`. Real POST to api.telegram.org/bot<token>/sendMessage.

    Sent with parse_mode=MarkdownV2 (CIN-102) so Gemini's **bold**
    output actually renders as bold instead of showing the literal
    asterisks -- to_telegram_markdown_v2 both converts CommonMark's
    double-asterisk to Telegram's single-asterisk syntax and escapes
    everything else MarkdownV2 treats as special.
    """
    url = f"{_TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    json_body = {
        "chat_id": chat_id,
        "text": to_telegram_markdown_v2(text),
        "parse_mode": "MarkdownV2",
    }
    response = (
        client.post(url, json=json_body, timeout=15.0)
        if client is not None
        else httpx.post(url, json=json_body, timeout=15.0)
    )
    return _handle_response(response)


def send_photo(
    chat_id: str, photo_url: str, caption: str, bot_token: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Publish a photo with `caption` to `chat_id`. Real POST to .../sendPhoto."""
    url = f"{_TELEGRAM_API_BASE}/bot{bot_token}/sendPhoto"
    json_body = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": to_telegram_markdown_v2(caption),
        "parse_mode": "MarkdownV2",
    }
    response = (
        client.post(url, json=json_body, timeout=15.0)
        if client is not None
        else httpx.post(url, json=json_body, timeout=15.0)
    )
    return _handle_response(response)


def send_video(
    chat_id: str, video_url: str, caption: str, bot_token: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Publish a video with `caption` to `chat_id`. Real POST to .../sendVideo."""
    url = f"{_TELEGRAM_API_BASE}/bot{bot_token}/sendVideo"
    json_body = {
        "chat_id": chat_id,
        "video": video_url,
        "caption": to_telegram_markdown_v2(caption),
        "parse_mode": "MarkdownV2",
    }
    response = (
        client.post(url, json=json_body, timeout=15.0)
        if client is not None
        else httpx.post(url, json=json_body, timeout=15.0)
    )
    return _handle_response(response)


def publish(account: SocialAccount, post: Post) -> dict[str, Any]:
    """Registered in app.scheduler.registry as the telegram publisher.

    Unlike Instagram, Telegram doesn't require media -- video_url/
    image_url just switch sendMessage for sendVideo/sendPhoto+caption
    when present (a Post carries at most one of the two, see CIN-93).
    """
    bot_token = get_access_token(account)
    if post.video_url:
        return send_video(account.external_account_id, post.video_url, post.text, bot_token)
    if post.image_url:
        return send_photo(account.external_account_id, post.image_url, post.text, bot_token)
    return send_message(account.external_account_id, post.text, bot_token)
