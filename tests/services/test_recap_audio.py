from datetime import date

import pytest

from connectors.market_recap import MarketRecapDto
from connectors.ticker_recap import TickerRecapDto
from connectors.tts import SynthesisResult
from services.recap_audio import (
    RecapAudioReader,
    RecapAudioService,
    language_for_market,
    validate_script_figures,
)


class FakeScriptWriter:
    def __init__(self, script: str = "Spoken script covering 202.81 and 2.21 percent."):
        self.script = script
        self.calls: list[dict] = []

    async def write(self, *, period, summary, bullets, language="en"):
        self.calls.append({"period": period, "summary": summary, "bullets": bullets, "language": language})
        return self.script


class FakeTts:
    def __init__(self):
        self.calls: list[dict] = []

    async def synthesize(self, text, *, voice="nova", instructions=None):
        self.calls.append({"text": text, "voice": voice})
        return SynthesisResult(audio=b"\x00" * 16000, content_type="audio/mpeg", duration_s=1.0)


class FakeStorage:
    def __init__(self):
        self.uploads: list[tuple[str, int]] = []

    def upload(self, key, data, *, content_type="audio/mpeg"):
        self.uploads.append((key, len(data)))
        return key

    def signed_url(self, key, **_):
        return f"https://signed.example/{key}"


class FakeRecapConnector:
    def __init__(self):
        self.saved: list[dict] = []

    def set_audio(self, recap_id, *, audio_key, audio_duration_s):
        self.saved.append({"id": recap_id, "key": audio_key, "duration": audio_duration_s})
        return True


def _market_dto(**overrides):
    defaults = dict(
        id=260,
        market="US",
        cadence="weekly",
        period_start=date(2026, 7, 13),
        period_end=date(2026, 7, 17),
        summary="Markets fell.",
        bullets=[{"text": "Nasdaq dropped 2.9 percent."}],
        sources=[],
    )
    defaults.update(overrides)
    return MarketRecapDto(**defaults)


def _ticker_dto(**overrides):
    defaults = dict(
        id=42,
        ticker="NVDA",
        cadence="daily",
        period_start=date(2026, 7, 17),
        period_end=date(2026, 7, 17),
        summary="NVDA fell.",
        bullets=[{"text": "Shares declined 2.21 percent."}],
        sources=[],
        price_change=None,
        search_query=None,
        created_at=None,
    )
    defaults.update(overrides)
    return TickerRecapDto(**defaults)


def _service(**overrides):
    kwargs = dict(
        script_writer=FakeScriptWriter(),
        tts=FakeTts(),
        storage=FakeStorage(),
        market_connector=FakeRecapConnector(),
        ticker_connector=FakeRecapConnector(),
    )
    kwargs.update(overrides)
    return RecapAudioService(**kwargs), kwargs


class TestValidateScriptFigures:
    def test_reports_figure_dropped_when_script_keeps_digits(self):
        warnings = validate_script_figures(
            "Nasdaq fell 2.9 percent; the S&P fell 1.6 percent.",
            "Nasdaq fell 2.9 percent.",
        )
        assert any("16" in w for w in warnings)

    def test_known_limitation_spelled_out_corruption_is_undetectable(self):
        # The rewrite corrupting 40.89 -> "forty point nine" is the failure mode
        # observed in the spike, and digit comparison CANNOT catch it: the script
        # has no digits to compare. Documented so nobody mistakes this check for
        # real fidelity validation. Catching this needs semantic comparison.
        warnings = validate_script_figures(
            "VN-Index fell 40.89 points.",
            "The index fell forty point nine points.",
        )
        assert warnings == []

    def test_no_warning_when_figures_preserved(self):
        assert validate_script_figures("Nasdaq fell 2.9 percent.", "Nasdaq fell 2.9 percent today.") == []

    def test_separator_styles_compare_equal(self):
        # Vietnamese 1.787,45 and English 1,787.45 are the same figure.
        assert validate_script_figures("closed at 1.787,45", "closed at 1,787.45") == []

    def test_fully_spelled_out_script_is_not_flagged(self):
        # The rewrite prompt asks for numbers as words, so a script with no digits
        # is the expected happy path -- not 14 missing figures.
        source = "Nasdaq 100 fell 2.9 percent on 2026-07-17."
        script = "The Nasdaq one hundred fell two point nine percent."
        assert validate_script_figures(source, script) == []


class TestLanguageForMarket:
    @pytest.mark.parametrize("market,expected", [("VN", "vi"), ("vn", "vi"), ("US", "en"), ("FI", "en")])
    def test_maps_market_to_language(self, market, expected):
        assert language_for_market(market) == expected


class TestGenerateForMarketRecap:
    @pytest.mark.asyncio
    async def test_uploads_and_persists_audio(self):
        service, deps = _service()
        result = await service.generate_for_market_recap(_market_dto())

        assert deps["storage"].uploads == [("market/US/weekly/2026-07-13-260.mp3", 16000)]
        assert deps["market_connector"].saved == [
            {"id": 260, "key": "market/US/weekly/2026-07-13-260.mp3", "duration": 1.0}
        ]
        assert result.audio_key == "market/US/weekly/2026-07-13-260.mp3"

    @pytest.mark.asyncio
    async def test_vietnamese_market_uses_vi_prompt(self):
        writer = FakeScriptWriter()
        service, _ = _service(script_writer=writer)
        await service.generate_for_market_recap(_market_dto(market="VN"))

        assert writer.calls[0]["language"] == "vi"

    @pytest.mark.asyncio
    async def test_empty_script_raises_without_uploading(self):
        service, deps = _service(script_writer=FakeScriptWriter(script=""))

        with pytest.raises(ValueError):
            await service.generate_for_market_recap(_market_dto())

        assert deps["storage"].uploads == []
        assert deps["market_connector"].saved == []


class TestGenerateForTickerRecap:
    @pytest.mark.asyncio
    async def test_uploads_under_ticker_key_and_persists(self):
        service, deps = _service()
        result = await service.generate_for_ticker_recap(_ticker_dto())

        assert result.audio_key == "ticker/NVDA/daily/2026-07-17-42.mp3"
        assert deps["ticker_connector"].saved[0]["id"] == 42


class TestRecapAudioReader:
    def test_returns_none_when_no_audio_generated(self):
        assert RecapAudioReader(storage=FakeStorage()).playback(None, None) is None

    def test_returns_signed_url_and_duration(self):
        playback = RecapAudioReader(storage=FakeStorage()).playback("market/US/daily/x.mp3", 78.8)
        assert playback == {"url": "https://signed.example/market/US/daily/x.mp3", "duration_s": 78.8}

    def test_survives_storage_failure(self):
        class BrokenStorage(FakeStorage):
            def signed_url(self, key, **_):
                raise RuntimeError("no credentials")

        # A signing failure must not take down the whole brief response.
        assert RecapAudioReader(storage=BrokenStorage()).playback("k.mp3", 1.0) is None
