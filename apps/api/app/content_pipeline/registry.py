from collections.abc import Callable
from typing import Any

from app.models import GenerationContentType

Generator = Callable[[dict[str, Any]], dict[str, Any]]

_REGISTRY: dict[GenerationContentType, Generator] = {}


def register_generator(content_type: GenerationContentType, generator: Generator) -> None:
    _REGISTRY[content_type] = generator


def get_generator(content_type: GenerationContentType) -> Generator:
    try:
        return _REGISTRY[content_type]
    except KeyError:
        raise NotImplementedError(f"Нет генератора для {content_type}") from None
