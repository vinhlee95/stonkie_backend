from datetime import UTC, date, datetime

import pytest

from ai_models.model_name import ModelName
from ai_models.openrouter_client import get_openrouter_model_name
from services.market_recap import recap_generator as market_generator
from services.market_recap.schemas import Candidate, RetrievalResult, RetrievalStats
from services.ticker_recap import recap_generator as ticker_generator

RECAP_JSON = '[RECAP_JSON]{"summary":"s","bullets":[{"text":"b","source_indices":[0]}]}[/RECAP_JSON]'
DEEPSEEK_SLUG = "deepseek/deepseek-v4.1-flash"


class FakeMultiAgent:
    """Records the model the generator asks for, then serves canned output without calling OpenRouter."""

    def __init__(self, model_name=None):
        self.model_name = get_openrouter_model_name(model_name) if model_name else "unpinned-default"

    def generate_content(self, prompt: str, use_google_search: bool = False):
        return [RECAP_JSON]


def _retrieval() -> RetrievalResult:
    candidate = Candidate(
        title="Reuters A",
        url="https://www.reuters.com/markets/a",
        snippet="snippet",
        published_date=datetime(2026, 11, 5, 12, 0, tzinfo=UTC),
        raw_content="Body A",
        score=0.9,
        provider="brave",
    )
    stats = RetrievalStats(
        queries_total=1, results_total=1, deduped=1, with_raw_content=1, allowlisted=1, ranked_top_k=1
    )
    return RetrievalResult(candidates=[candidate], stats=stats)


@pytest.mark.parametrize("cadence", ["daily", "weekly"])
def test_market_recap_uses_deepseek_for_every_cadence(monkeypatch, cadence):
    monkeypatch.setattr(market_generator, "MultiAgent", FakeMultiAgent)

    result = market_generator.generate_recap(
        _retrieval(), market="US", cadence=cadence, period_start=date(2026, 11, 2), period_end=date(2026, 11, 6)
    )

    assert result.model == DEEPSEEK_SLUG


def test_ticker_recap_uses_deepseek(monkeypatch):
    monkeypatch.setattr(ticker_generator, "MultiAgent", FakeMultiAgent)

    result = ticker_generator.generate_recap(
        _retrieval(),
        ticker="NVDA",
        company_name="NVIDIA Corporation",
        price_change=None,
        period_start=date(2026, 11, 5),
        period_end=date(2026, 11, 5),
    )

    assert result.model == DEEPSEEK_SLUG


def test_deepseek_maps_to_openrouter_slug():
    assert get_openrouter_model_name(ModelName.DeepSeekV41Flash) == DEEPSEEK_SLUG
