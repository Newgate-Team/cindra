from collections.abc import Callable
from typing import Any

from app.models import SocialAccount, SocialPlatform

# A publisher sends `text` via the given connected account and returns
# the platform's raw response dict (used to pull out a message id).
Publisher = Callable[[SocialAccount, str], dict[str, Any]]

_REGISTRY: dict[SocialPlatform, Publisher] = {}


def register_publisher(platform: SocialPlatform, publisher: Publisher) -> None:
    _REGISTRY[platform] = publisher


def get_publisher(platform: SocialPlatform) -> Publisher:
    try:
        return _REGISTRY[platform]
    except KeyError:
        raise NotImplementedError(f"Нет адаптера публикации для {platform}") from None
