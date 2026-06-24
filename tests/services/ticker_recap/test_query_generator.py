import pytest

from services.ticker_recap.query_generator import QueryGenerationError, generate_query


class FakeAgent:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []
        self.model_name = "fake-model"

    def generate_content(self, prompt: str, use_google_search: bool = False):
        self.calls.append({"prompt": prompt, "use_google_search": use_google_search})
        return self.chunks


def _price_change(change_percent: float) -> dict:
    return {
        "trading_date": "2026-06-18",
        "close": 200.0,
        "prev_close": 190.0,
        "change": 10.0,
        "change_percent": change_percent,
        "currency": "USD",
    }


class TestGenerateQuery:
    def test_returns_parsed_llm_query(self):
        agent = FakeAgent(["why did AAPL jump 5% today"])

        query = generate_query("AAPL", "Apple Inc.", _price_change(5.0), agent=agent)

        assert query == "why did AAPL jump 5% today"

    def test_big_move_prompts_causal_framing_with_move(self):
        agent = FakeAgent(["why did TSLA fall 8% today"])

        generate_query("TSLA", "Tesla, Inc.", _price_change(-8.0), agent=agent)

        prompt = agent.calls[0]["prompt"]
        assert "BIG move" in prompt
        assert "causal" in prompt
        assert "-8.00%" in prompt

    def test_flat_move_prompts_neutral_framing(self):
        agent = FakeAgent(["latest NVDA stock news today"])

        generate_query("NVDA", "NVIDIA Corporation", _price_change(0.4), agent=agent)

        prompt = agent.calls[0]["prompt"]
        assert "small/flat move" in prompt
        assert "neutral" in prompt
        assert "BIG move" not in prompt

    def test_missing_price_change_falls_back_to_neutral(self):
        agent = FakeAgent(["latest GOOG news"])

        query = generate_query("GOOG", "Alphabet Inc.", None, agent=agent)

        assert query == "latest GOOG news"
        prompt = agent.calls[0]["prompt"]
        assert "No price data available" in prompt
        assert "BIG move" not in prompt

    def test_none_change_percent_falls_back_to_neutral(self):
        agent = FakeAgent(["latest GOOG news"])

        generate_query("GOOG", "Alphabet Inc.", {"change_percent": None}, agent=agent)

        assert "No price data available" in agent.calls[0]["prompt"]

    def test_empty_llm_output_raises_typed_error(self):
        agent = FakeAgent(["   \n  "])

        with pytest.raises(QueryGenerationError):
            generate_query("AAPL", "Apple Inc.", _price_change(5.0), agent=agent)

    def test_drops_citation_dicts_when_joining(self):
        agent = FakeAgent([{"type": "url_citation", "url": "x"}, "why did AAPL move"])

        query = generate_query("AAPL", "Apple Inc.", _price_change(5.0), agent=agent)

        assert query == "why did AAPL move"
