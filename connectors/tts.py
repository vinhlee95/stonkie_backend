"""Text-to-speech connector.

All TTS I/O lives here; services inject this connector and never touch the OpenAI
SDK directly. Engine is swappable behind `TtsConnector` (ElevenLabs / Kokoro can
be added as sibling classes implementing `synthesize`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_VOICE = "nova"

# Roughly 128kbps CBR mp3 -> bytes/sec. Used to estimate duration without
# pulling in an audio-decoding dependency just to read a header.
_MP3_BYTES_PER_SECOND = 128_000 / 8

VOICE_INSTRUCTIONS = (
    "You are a warm, engaging financial news podcast host. Speak naturally and "
    "conversationally, professional but friendly, with an enthusiastic yet relaxed "
    "pace. Use natural transitions between market stories."
)


@dataclass(frozen=True)
class SynthesisResult:
    audio: bytes
    content_type: str
    duration_s: float


class TtsConnector:
    """Synthesizes speech via OpenAI. Owns its SDK client."""

    def __init__(self, client: AsyncOpenAI | None = None, *, model: str = TTS_MODEL) -> None:
        self._client = client or AsyncOpenAI()
        self._model = model

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = DEFAULT_VOICE,
        instructions: str = VOICE_INSTRUCTIONS,
    ) -> SynthesisResult:
        chunks: list[bytes] = []
        async with self._client.audio.speech.with_streaming_response.create(
            model=self._model,
            voice=voice,
            input=text,
            instructions=instructions,
            response_format="mp3",
        ) as response:
            async for chunk in response.iter_bytes():
                chunks.append(chunk)

        audio = b"".join(chunks)
        return SynthesisResult(
            audio=audio,
            content_type="audio/mpeg",
            duration_s=round(len(audio) / _MP3_BYTES_PER_SECOND, 1),
        )
