"""The avatar's voice: Gemini TTS through the Interactions API.

Returns raw PCM16 mono at 24 kHz -- exactly what LiveAvatar's LITE mode
takes over its WebSocket, so the agent can forward it untouched. The
emotion picked with the reply becomes a delivery style; TTS models read
`text` strictly as a transcript, so the style must not go inside it.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from app.config import get_settings
from app.content_pipeline.errors import TransientGenerationError

SAMPLE_RATE = 24000
_RETRYABLE = {429, 500, 502, 503, 504}
_STYLES = {
    "neutral": "friendly and lively, like a live-stream host",
    "happy": "cheerful and warm",
    "laughing": "amused, smiling, with a light laugh in the voice",
    "surprised": "surprised and excited",
    "thinking": "thoughtful and calm",
    "sad": "gently sympathetic",
}


class SpeechError(Exception):
    """Not retryable: bad key/model, empty or malformed audio."""


def _audio_from(body: dict[str, Any]) -> bytes:
    """The last audio block of the model's output (REST shape:
    steps[].content[] with type "audio" and base64 `data`)."""
    blocks = [
        item
        for step in body.get("steps", [])
        if step.get("type") == "model_output"
        for item in step.get("content", [])
        if item.get("type") == "audio" and item.get("data")
    ]
    if not blocks:
        raise SpeechError("Gemini TTS не вернул аудио")
    audio = base64.b64decode(blocks[-1]["data"])
    if audio[:4] == b"RIFF":
        # A WAV came back after all (the default for unary calls):
        # skip to the PCM in its "data" chunk.
        marker = audio.find(b"data", 12)
        if marker < 0:
            raise SpeechError("Gemini TTS вернул повреждённый WAV")
        audio = audio[marker + 8 :]
    if len(audio) < 2:
        raise SpeechError("Gemini TTS вернул пустое аудио")
    return audio[: len(audio) - len(audio) % 2]


def synthesize(
    text: str, *, voice: str, emotion: str = "neutral", client: httpx.Client | None = None
) -> bytes:
    settings = get_settings()
    body = {
        "model": settings.gemini_tts_model,
        "input": [
            {
                "type": "user_input",
                "content": [
                    {
                        "type": "text",
                        "text": text,
                        "annotations": [
                            {"type": "speech_metadata", "style": _STYLES.get(emotion, _STYLES["neutral"])}
                        ],
                    }
                ],
            }
        ],
        "response_format": {"type": "audio", "mime_type": "audio/l16", "sample_rate": SAMPLE_RATE},
        "generation_config": {"speech_config": [{"voice": voice}]},
    }
    url = f"{settings.gemini_api_base_url.rstrip('/')}/v1beta/interactions"
    # Key in a header, not ?key= (CIN-111: a URL with the key can end up
    # in an error message).
    kwargs: dict[str, Any] = {
        "json": body,
        "headers": {"x-goog-api-key": settings.gemini_api_key},
        "timeout": 30.0,
    }
    try:
        response = client.post(url, **kwargs) if client else httpx.post(url, **kwargs)
    except httpx.TransportError as exc:
        raise TransientGenerationError(f"Gemini TTS недоступен: {exc}") from exc
    if response.status_code in _RETRYABLE:
        raise TransientGenerationError(f"Gemini TTS {response.status_code}")
    if response.status_code >= 400:
        raise SpeechError(f"Gemini TTS {response.status_code}: {response.text[:300]}")
    return _audio_from(response.json())
