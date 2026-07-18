"""Generate and serve spoken audio for market and ticker recaps.

Pipeline per recap: fetch DTO -> speakable rewrite -> figure-fidelity check ->
TTS -> upload to object storage -> persist `audio_key` + duration.

Connectors are injected so tests can supply fakes (no network in tests).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from connectors.audio_storage import AudioStorageConnector
from connectors.market_recap import MarketRecapConnector, MarketRecapDto
from connectors.script_writer import ScriptWriterConnector
from connectors.ticker_recap import TickerRecapConnector, TickerRecapDto
from connectors.tts import DEFAULT_VOICE, TtsConnector

logger = logging.getLogger(__name__)

# Markets whose recap text is written in a non-English language.
MARKET_LANGUAGE = {"VN": "vi"}
DEFAULT_LANGUAGE = "en"

_NUMBER_RE = re.compile(r"\d[\d.,]*")


@dataclass(frozen=True)
class AudioResult:
    recap_id: int
    audio_key: str
    duration_s: float
    figure_warnings: list[str]


def language_for_market(market: str) -> str:
    return MARKET_LANGUAGE.get(market.upper(), DEFAULT_LANGUAGE)


def _normalize_number(raw: str) -> str:
    """Strip grouping/decimal separators so 1.787,45 and 1,787.45 compare equal."""
    return raw.rstrip(".,").replace(".", "").replace(",", "")


def validate_script_figures(source_text: str, script: str) -> list[str]:
    """Report figures present in the source recap but absent from the spoken script.

    The rewrite model is known to round and occasionally invent numbers (see
    .claude/plans/recap-audio-backend.md).

    Only applicable when the script still uses digits. The rewrite prompts ask for
    numbers spelled out as words ("2.21%" -> "two point two one percent"), and a
    fully spelled-out script legitimately contains no digits at all -- comparing
    digit tokens there produces a warning for every figure in the recap, which is
    noise rather than signal. In that case the check returns no warnings.
    """
    script_numbers = {_normalize_number(m) for m in _NUMBER_RE.findall(script)}
    if not script_numbers:
        return []
    source_numbers = {_normalize_number(m) for m in _NUMBER_RE.findall(source_text)}
    missing = sorted(n for n in source_numbers - script_numbers if n)
    return [f"figure {n} in recap but not in script" for n in missing]


def _recap_text(summary: str, bullets: list[dict]) -> str:
    lines = [b.get("text", "") for b in bullets]
    return summary + "\n\n" + "\n".join(f"- {line}" for line in lines if line)


class RecapAudioService:
    def __init__(
        self,
        *,
        script_writer: ScriptWriterConnector | None = None,
        tts: TtsConnector | None = None,
        storage: AudioStorageConnector | None = None,
        market_connector: MarketRecapConnector | None = None,
        ticker_connector: TickerRecapConnector | None = None,
        voice: str = DEFAULT_VOICE,
    ) -> None:
        self._script_writer = script_writer or ScriptWriterConnector()
        self._tts = tts or TtsConnector()
        self._storage = storage or AudioStorageConnector()
        self._market = market_connector or MarketRecapConnector()
        self._ticker = ticker_connector or TickerRecapConnector()
        self._voice = voice

    async def _generate(
        self,
        *,
        recap_id: int,
        period: str,
        summary: str,
        bullets: list[dict],
        language: str,
        key: str,
    ) -> AudioResult:
        bullet_texts = [b.get("text", "") for b in bullets if b.get("text")]
        script = await self._script_writer.write(
            period=period,
            summary=summary,
            bullets=bullet_texts,
            language=language,
        )
        if not script:
            raise ValueError(f"empty script generated for recap {recap_id}")

        warnings = validate_script_figures(_recap_text(summary, bullets), script)
        if warnings and language == DEFAULT_LANGUAGE:
            logger.warning(
                "recap_audio.figure_mismatch recap_id=%s count=%d details=%s",
                recap_id,
                len(warnings),
                "; ".join(warnings[:5]),
            )

        synthesis = await self._tts.synthesize(script, voice=self._voice)
        self._storage.upload(key, synthesis.audio, content_type=synthesis.content_type)
        return AudioResult(
            recap_id=recap_id,
            audio_key=key,
            duration_s=synthesis.duration_s,
            figure_warnings=warnings,
        )

    async def generate_for_market_recap(self, dto: MarketRecapDto) -> AudioResult:
        key = f"market/{dto.market.upper()}/{dto.cadence}/{dto.period_start.isoformat()}-{dto.id}.mp3"
        result = await self._generate(
            recap_id=dto.id,
            period=f"{dto.period_start.isoformat()} to {dto.period_end.isoformat()}",
            summary=dto.summary,
            bullets=dto.bullets,
            language=language_for_market(dto.market),
            key=key,
        )
        self._market.set_audio(dto.id, audio_key=result.audio_key, audio_duration_s=result.duration_s)
        return result

    async def generate_for_ticker_recap(self, dto: TickerRecapDto) -> AudioResult:
        key = f"ticker/{dto.ticker.upper()}/{dto.cadence}/{dto.period_start.isoformat()}-{dto.id}.mp3"
        result = await self._generate(
            recap_id=dto.id,
            period=f"{dto.period_start.isoformat()} to {dto.period_end.isoformat()}",
            summary=dto.summary,
            bullets=dto.bullets,
            language=DEFAULT_LANGUAGE,
            key=key,
        )
        self._ticker.set_audio(dto.id, audio_key=result.audio_key, audio_duration_s=result.duration_s)
        return result


class RecapAudioReader:
    """Read-side service: mints playable URLs for already-generated audio.

    Routers depend on this rather than touching connectors directly.
    """

    def __init__(self, storage: AudioStorageConnector | None = None) -> None:
        self._storage = storage or AudioStorageConnector()

    def playback(self, audio_key: str | None, duration_s: float | None) -> dict | None:
        if not audio_key:
            return None
        try:
            url = self._storage.signed_url(audio_key)
        except Exception:
            logger.exception("recap_audio.signed_url_failed key=%s", audio_key)
            return None
        return {"url": url, "duration_s": duration_s}
