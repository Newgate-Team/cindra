from collections.abc import Callable
from typing import Any

from app.models import Post, SocialAccount, SocialPlatform

# A publisher sends `post` via the given connected account and returns
# the platform's raw response dict (used to pull out a message id).
# Takes the whole Post (not just its text) because platforms differ
# in what they need -- Telegram can publish text alone, Instagram
# requires post.image_url and has no text-only post at all.
Publisher = Callable[[SocialAccount, Post], dict[str, Any]]

_REGISTRY: dict[SocialPlatform, Publisher] = {}


def register_publisher(platform: SocialPlatform, publisher: Publisher) -> None:
    _REGISTRY[platform] = publisher


def get_publisher(platform: SocialPlatform) -> Publisher:
    try:
        return _REGISTRY[platform]
    except KeyError:
        raise NotImplementedError(f"Нет адаптера публикации для {platform}") from None
