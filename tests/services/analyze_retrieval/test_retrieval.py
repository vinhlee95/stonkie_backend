from datetime import UTC, datetime

import pytest

from services.analyze_retrieval import retrieval
from services.analyze_retrieval.retrieval import retrieve_for_analyze
from services.analyze_retrieval.schemas import BraveRetrievalError
from services.market_recap.schemas import Candidate
from services.market_recap.url_utils import source_id_for


class _StubBraveClient:
    def __init__(self, candidates: list[Candidate]) -> None:
        self._candidates = candidates

    def search(
        self,
        *,
        query: str,
        country: str,
        search_lang: str,
        goggle: str,
        count: int = 20,
    ) -> list[Candidate]:
        _ = (query, country, search_lang, goggle, count)
        return self._candidates


def _candidate(url: str, raw_content: str, score: float) -> Candidate:
    return Candidate(
        title="title",
        url=url,
        snippet="snippet",
        published_date=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
        raw_content=raw_content,
        score=score,
        provider="brave",
    )


def test_retrieve_for_analyze_dedupes_filters_topk_and_preserves_stable_source_ids() -> None:
    stub = _StubBraveClient(
        candidates=[
            _candidate("https://www.reuters.com/a?utm_source=test", "reuters body", 0.1),
            _candidate("https://www.reuters.com/a", "duplicate body", 10.0),
            _candidate("https://investing.com/b", "", 5.0),
            _candidate("https://www.cnbc.com/c", "cnbc body", 0.2),
        ]
    )

    result = retrieve_for_analyze(
        question="How is sentiment?",
        market="GLOBAL",
        request_id="req-1",
        brave_client=stub,
        top_k=5,
    )

    assert [source.url for source in result.sources] == [
        "https://www.reuters.com/a",
        "https://www.cnbc.com/c",
    ]
    assert [source.id for source in result.sources] == [
        source_id_for("https://www.reuters.com/a"),
        source_id_for("https://www.cnbc.com/c"),
    ]
    assert result.sources[0].publisher == "Reuters"
    assert result.sources[1].publisher == "CNBC"
    assert all(source.is_trusted for source in result.sources)


def test_retrieve_for_analyze_raises_on_empty_after_filtering() -> None:
    stub = _StubBraveClient(candidates=[_candidate("https://investing.com/b", "", 1.0)])
    with pytest.raises(BraveRetrievalError):
        retrieve_for_analyze(
            question="q",
            market="GLOBAL",
            request_id="req-2",
            brave_client=stub,
        )


def test_retrieve_for_analyze_keeps_best_duplicate_by_canonical_url() -> None:
    stub = _StubBraveClient(
        candidates=[
            _candidate("https://example.com/a?ref=1", "short", 0.1),
            _candidate("https://example.com/a", "longer and better", 0.9),
        ]
    )

    result = retrieve_for_analyze(
        question="q",
        market="GLOBAL",
        request_id="req-3",
        brave_client=stub,
        top_k=5,
    )

    assert [source.url for source in result.sources] == ["https://example.com/a"]


def test_retrieve_for_analyze_logs_observability_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubBraveClient(
        candidates=[
            _candidate("https://reuters.com/a", "body a", 0.9),
            _candidate("https://cnbc.com/b", "body b", 0.8),
        ]
    )
    captured: dict[str, object] = {}

    def _capture_log(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(retrieval, "log_retrieval", _capture_log)

    result = retrieve_for_analyze(
        question="How is AAPL doing?",
        market="GLOBAL",
        request_id="req-log-1",
        brave_client=stub,
        ticker="AAPL",
        brave_latency_ms=42,
    )

    assert len(result.sources) == 2
    assert captured == {
        "request_id": "req-log-1",
        "ticker": "AAPL",
        "market": "GLOBAL",
        "ranked_urls": ["https://reuters.com/a", "https://cnbc.com/b"],
        "selected_source_ids": [result.sources[0].id, result.sources[1].id],
        "brave_latency_ms": 42,
        "raw_brave_response": None,
    }
