from datetime import UTC, datetime, timedelta

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
        freshness: str | None = None,
    ) -> list[Candidate]:
        _ = (query, country, search_lang, goggle, count, freshness)
        return self._candidates


def _candidate(
    url: str,
    raw_content: str,
    score: float,
    *,
    published_date: datetime | None = None,
) -> Candidate:
    return Candidate(
        title="title",
        url=url,
        snippet="snippet",
        published_date=published_date or (datetime.now(UTC) - timedelta(days=1)),
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


def test_retrieve_for_analyze_propagates_raw_content_to_analyze_source() -> None:
    stub = _StubBraveClient(
        candidates=[
            _candidate("https://reuters.com/a", "Fed held rates at 3.5-3.75%.", 0.9),
            _candidate("https://cnbc.com/b", "Powell hinted at policy easing.", 0.8),
        ]
    )

    result = retrieve_for_analyze(
        question="What did the Fed do?",
        market="GLOBAL",
        request_id="req-rc",
        brave_client=stub,
    )

    assert {source.raw_content for source in result.sources} == {
        "Fed held rates at 3.5-3.75%.",
        "Powell hinted at policy easing.",
    }


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


def test_retrieve_for_analyze_prefers_trusted_domains_and_backfills_untrusted() -> None:
    stub = _StubBraveClient(
        candidates=[
            _candidate("https://random.example.com/1", "untrusted one", 1.0),
            _candidate("https://www.reuters.com/a", "trusted one", 1.0),
            _candidate("https://another.example.com/2", "untrusted two", 1.0),
        ]
    )

    result = retrieve_for_analyze(
        question="Company background overview",
        market="GLOBAL",
        request_id="req-trust-1",
        brave_client=stub,
        top_k=3,
    )

    assert [source.url for source in result.sources] == [
        "https://www.reuters.com/a",
        "https://random.example.com/1",
        "https://another.example.com/2",
    ]
    assert [source.is_trusted for source in result.sources] == [True, False, False]


def test_retrieve_for_analyze_applies_one_per_domain_on_first_pass() -> None:
    stub = _StubBraveClient(
        candidates=[
            _candidate("https://www.cnbc.com/a", "cnbc one", 1.0),
            _candidate("https://www.cnbc.com/b", "cnbc two", 1.0),
            _candidate("https://www.reuters.com/c", "reuters one", 1.0),
            _candidate("https://www.wsj.com/d", "wsj one", 1.0),
        ]
    )

    result = retrieve_for_analyze(
        question="Latest market news",
        market="GLOBAL",
        request_id="req-domain-1",
        brave_client=stub,
        top_k=3,
    )

    assert [source.url for source in result.sources] == [
        "https://www.cnbc.com/a",
        "https://www.reuters.com/c",
        "https://www.wsj.com/d",
    ]


def test_retrieve_for_analyze_drops_old_results_for_fresh_questions() -> None:
    stub = _StubBraveClient(
        candidates=[
            _candidate(
                "https://www.reuters.com/old",
                "old but trusted",
                1.0,
                published_date=datetime.now(UTC) - timedelta(days=90),
            ),
            _candidate(
                "https://www.cnbc.com/new",
                "fresh trusted",
                1.0,
                published_date=datetime.now(UTC) - timedelta(days=1),
            ),
        ]
    )

    result = retrieve_for_analyze(
        question="latest earnings news for AAPL",
        market="GLOBAL",
        request_id="req-fresh-1",
        brave_client=stub,
        top_k=5,
    )

    assert [source.url for source in result.sources] == ["https://www.cnbc.com/new"]


def test_retrieve_for_analyze_keeps_old_authoritative_results_for_evergreen_questions() -> None:
    stub = _StubBraveClient(
        candidates=[
            _candidate(
                "https://www.reuters.com/old",
                "old but useful",
                1.0,
                published_date=datetime.now(UTC) - timedelta(days=400),
            ),
        ]
    )

    result = retrieve_for_analyze(
        question="Explain Apple's business model",
        market="GLOBAL",
        request_id="req-evergreen-1",
        brave_client=stub,
        top_k=5,
    )

    assert [source.url for source in result.sources] == ["https://www.reuters.com/old"]


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
        "freshness": "pm",
        "returned_candidates": 2,
        "unique_candidates": 2,
        "unique_domains": 2,
        "selected_domains": ["reuters.com", "cnbc.com"],
        "selected_source_ages": ["pw", "pw"],
        "stale_dropped": 0,
        "trusted_selected": 2,
        "used_untrusted_backfill": False,
        "raw_brave_response": None,
    }
