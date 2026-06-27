from datetime import UTC, date, datetime

from services.market_recap.schemas import Candidate, RetrievalResult, RetrievalStats


class FakeAgent:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []
        self.model_name = "fake-model"

    def generate_content(self, prompt: str, use_google_search: bool = False):
        self.calls.append({"prompt": prompt, "use_google_search": use_google_search})
        return self.chunks


def _candidate(title: str, url: str, raw_content: str) -> Candidate:
    return Candidate(
        title=title,
        url=url,
        snippet="snippet",
        published_date=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
        raw_content=raw_content,
        score=0.9,
        provider="brave",
    )


def _retrieval() -> RetrievalResult:
    return RetrievalResult(
        candidates=[
            _candidate("Apple climbs on iPhone demand", "https://www.reuters.com/markets/aapl", "Body A"),
            _candidate("Analysts raise AAPL targets", "https://apnews.com/article/aapl", "Body B"),
        ],
        stats=RetrievalStats(
            queries_total=1,
            results_total=2,
            deduped=2,
            with_raw_content=2,
            allowlisted=2,
            ranked_top_k=2,
        ),
    )


def _price_change(change_percent: float = 5.0) -> dict:
    return {
        "trading_date": "2026-06-18",
        "close": 200.0,
        "prev_close": 190.0,
        "change": 10.0,
        "change_percent": change_percent,
        "currency": "USD",
    }


class TestGenerateRecap:
    def test_tracer_valid_json_produces_payload(self):
        from services.ticker_recap.recap_generator import generate_recap

        agent = FakeAgent(
            chunks=[
                '[RECAP_JSON]{"summary":"Apple rose on strong demand.",'
                '"bullets":[{"text":"iPhone demand drove the move","source_indices":[0]}]}[/RECAP_JSON]'
            ]
        )

        result = generate_recap(
            _retrieval(),
            ticker="AAPL",
            company_name="Apple Inc.",
            price_change=_price_change(),
            period_start=date(2026, 6, 18),
            period_end=date(2026, 6, 18),
            agent=agent,
        )

        assert result.payload.ticker == "AAPL"
        assert result.payload.cadence == "daily"
        assert result.payload.summary == "Apple rose on strong demand."
        assert len(result.payload.bullets) == 1
        assert len(result.payload.sources) == 1

    def test_source_indices_map_to_correct_candidates(self):
        from services.market_recap.url_utils import source_id_for
        from services.ticker_recap.recap_generator import generate_recap

        retrieval = _retrieval()
        agent = FakeAgent(
            chunks=[
                '[RECAP_JSON]{"summary":"s",'
                '"bullets":[{"text":"b0","source_indices":[0]},'
                '{"text":"b1","source_indices":[1]}]}[/RECAP_JSON]'
            ]
        )

        result = generate_recap(
            retrieval,
            ticker="AAPL",
            company_name="Apple Inc.",
            price_change=_price_change(),
            period_start=date(2026, 6, 18),
            period_end=date(2026, 6, 18),
            agent=agent,
        )

        source_ids = {s.id for s in result.payload.sources}
        assert source_ids == {source_id_for(c.url) for c in retrieval.candidates}
        assert result.payload.bullets[0].citations[0].source_id == source_id_for(retrieval.candidates[0].url)
        assert result.payload.bullets[1].citations[0].source_id == source_id_for(retrieval.candidates[1].url)

    def test_prompt_includes_ticker_price_context_and_indexed_corpus(self):
        from services.ticker_recap.recap_generator import generate_recap

        agent = FakeAgent(chunks=['[RECAP_JSON]{"summary":"s","bullets":[]}[/RECAP_JSON]'])
        try:
            generate_recap(
                _retrieval(),
                ticker="AAPL",
                company_name="Apple Inc.",
                price_change=_price_change(5.0),
                period_start=date(2026, 6, 18),
                period_end=date(2026, 6, 18),
                agent=agent,
            )
        except Exception:
            pass

        prompt = agent.calls[0]["prompt"]
        # single-ticker focus
        assert "AAPL" in prompt
        assert "Apple Inc." in prompt
        # price context
        assert "+5.00%" in prompt
        assert "200.0" in prompt
        assert "190.0" in prompt
        # indexed corpus
        assert "Source [0]" in prompt
        assert "Source [1]" in prompt
        assert "https://www.reuters.com/markets/aapl" in prompt
        assert "Body B" in prompt
        # schema instructions
        assert '"source_indices"' in prompt
        assert "[RECAP_JSON]" in prompt
        assert agent.calls[0]["use_google_search"] is False

    def test_prompt_handles_missing_price_change(self):
        from services.ticker_recap.recap_generator import generate_recap

        agent = FakeAgent(chunks=['[RECAP_JSON]{"summary":"s","bullets":[]}[/RECAP_JSON]'])
        try:
            generate_recap(
                _retrieval(),
                ticker="AAPL",
                company_name="Apple Inc.",
                price_change=None,
                period_start=date(2026, 6, 18),
                period_end=date(2026, 6, 18),
                agent=agent,
            )
        except Exception:
            pass

        prompt = agent.calls[0]["prompt"]
        assert "AAPL" in prompt
        assert "unavailable" in prompt.lower()

    def test_source_index_out_of_range_raises(self):
        import pytest

        from services.ticker_recap.recap_generator import GeneratorError, generate_recap

        agent = FakeAgent(
            chunks=['[RECAP_JSON]{"summary":"s","bullets":[{"text":"b","source_indices":[7]}]}[/RECAP_JSON]']
        )
        with pytest.raises(GeneratorError):
            generate_recap(
                _retrieval(),
                ticker="AAPL",
                company_name="Apple Inc.",
                price_change=_price_change(),
                period_start=date(2026, 6, 18),
                period_end=date(2026, 6, 18),
                agent=agent,
            )

    def test_missing_markers_raises(self):
        import pytest

        from services.ticker_recap.recap_generator import GeneratorError, generate_recap

        agent = FakeAgent(chunks=['{"summary":"s","bullets":[]}'])
        with pytest.raises(GeneratorError):
            generate_recap(
                _retrieval(),
                ticker="AAPL",
                company_name="Apple Inc.",
                price_change=_price_change(),
                period_start=date(2026, 6, 18),
                period_end=date(2026, 6, 18),
                agent=agent,
            )
