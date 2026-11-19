"""Real-time avatar services a live stream can be hosted by.

Each entry names the Integration (the user's own key, BYO) the stream
agent receives at claim time -- the avatar's minutes are billed to the
user's account with that vendor, never to Cindra.
"""

from dataclasses import dataclass

from app.models import IntegrationProvider


@dataclass(frozen=True)
class AvatarProvider:
    integration: IntegrationProvider
    label: str


# Voices of the speech synthesis Cindra feeds a LITE-mode avatar
# (Gemini TTS prebuilt voices) -> how the UI describes them.
TTS_VOICES: dict[str, str] = {
    "Kore": "уверенный, женский",
    "Aoede": "лёгкий, женский",
    "Leda": "молодой, женский",
    "Zephyr": "яркий, женский",
    "Despina": "мягкий, женский",
    "Puck": "бодрый, мужской",
    "Charon": "спокойный, мужской",
    "Fenrir": "энергичный, мужской",
    "Orus": "твёрдый, мужской",
    "Achird": "дружелюбный, мужской",
}
DEFAULT_TTS_VOICE = "Kore"

PROVIDERS: dict[str, AvatarProvider] = {
    # LITE mode: Cindra supplies the speech (its own TTS), LiveAvatar
    # animates the face -- half the credits of FULL mode.
    "liveavatar": AvatarProvider(IntegrationProvider.liveavatar, "LiveAvatar (HeyGen)"),
    # Same flow (our speech in, video out via LiveKit); ~$0.009/min vs
    # LiveAvatar's ~$0.09 -- the affordable option for round-the-clock.
    "simli": AvatarProvider(IntegrationProvider.simli, "Simli"),
}
