from datetime import UTC, date, datetime

from services.market_recap.schemas import Candidate
from services.ticker_recap.retrieval import retrieve_for_ticker


class FakeSearchProvider:
    def __init__(self, candidates: list[Candidate], snapshot: dict | None = None) -> None:
        self.candidates = candidates
        self.snapshot = snapshot or {}
        self.calls: list[dict] = []

    def search(
        self,
        query: str,
        period_start: date,
        period_end: date,
        include_domains: list[str] | None = None,
    ) -> list[Candidate]:
        candidates, _ = self.search_with_snapshot(
            query=query,
            period_start=period_start,
            period_end=period_end,
            include_domains=include_domains,
        )
        return candidates

    def search_with_snapshot(
        self,
        query: str,
        period_start: date,
        period_end: date,
        include_domains: list[str] | None = None,
    ) -> tuple[list[Candidate], dict]:
        self.calls.append({"query": query, "include_domains": include_domains})
        return self.candidates, self.snapshot


def _candidate(
    *,
    title: str,
    url: str,
    raw_content: str = "body",
    score: float = 0.5,
    published_date: datetime | None = None,
    provider: str = "brave",
    snippet: str = "snippet",
) -> Candidate:
    return Candidate(
        title=title,
        url=url,
        snippet=snippet,
        published_date=published_date or datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
        raw_content=raw_content,
        score=score,
        provider=provider,
    )


def test_retrieve_for_ticker_returns_in_window_candidates():
    provider = FakeSearchProvider(
        candidates=[
            _candidate(
                title="Apple climbs on strong iPhone demand",
                url="https://www.reuters.com/technology/apple-iphone",
            )
        ]
    )

    result = retrieve_for_ticker(
        ticker="AAPL",
        company_name="Apple Inc.",
        query="why did Apple stock rise today",
        period_start=date(2026, 6, 18),
        period_end=date(2026, 6, 18),
        search_provider=provider,
    )

    assert [candidate.title for candidate in result.candidates] == ["Apple climbs on strong iPhone demand"]
    assert provider.calls[0]["query"] == "why did Apple stock rise today"
    assert len(result.query_snapshots) == 1


def test_retrieve_for_ticker_drops_out_of_window_brave_candidates():
    provider = FakeSearchProvider(
        candidates=[
            _candidate(
                title="in-window",
                url="https://www.reuters.com/a",
                published_date=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
            ),
            _candidate(
                title="out-of-window",
                url="https://www.reuters.com/b",
                published_date=datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
            ),
        ]
    )

    result = retrieve_for_ticker(
        ticker="AAPL",
        company_name="Apple Inc.",
        query="latest Apple news",
        period_start=date(2026, 6, 18),
        period_end=date(2026, 6, 18),
        search_provider=provider,
    )

    assert [candidate.title for candidate in result.candidates] == ["in-window"]


def test_retrieve_for_ticker_drops_empty_raw_content():
    provider = FakeSearchProvider(
        candidates=[
            _candidate(title="has body", url="https://www.reuters.com/a", raw_content="body"),
            _candidate(title="blank body", url="https://www.reuters.com/b", raw_content="   "),
        ]
    )

    result = retrieve_for_ticker(
        ticker="AAPL",
        company_name="Apple Inc.",
        query="latest Apple news",
        period_start=date(2026, 6, 18),
        period_end=date(2026, 6, 18),
        search_provider=provider,
    )

    assert [candidate.title for candidate in result.candidates] == ["has body"]
    assert result.stats.with_raw_content == 1


def test_retrieve_for_ticker_dedupes_same_source_id():
    provider = FakeSearchProvider(
        candidates=[
            _candidate(
                title="lower score dup",
                url="https://www.reuters.com/shared?utm_source=x",
                score=0.2,
            ),
            _candidate(
                title="higher score dup",
                url="https://www.reuters.com/shared",
                score=0.9,
            ),
        ]
    )

    result = retrieve_for_ticker(
        ticker="AAPL",
        company_name="Apple Inc.",
        query="latest Apple news",
        period_start=date(2026, 6, 18),
        period_end=date(2026, 6, 18),
        search_provider=provider,
    )

    assert [candidate.title for candidate in result.candidates] == ["higher score dup"]
    assert result.stats.deduped == 1


def test_retrieve_for_ticker_returns_empty_when_no_in_window_candidates():
    provider = FakeSearchProvider(
        candidates=[
            _candidate(
                title="out-of-window only",
                url="https://www.reuters.com/a",
                published_date=datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
            )
        ]
    )

    result = retrieve_for_ticker(
        ticker="AAPL",
        company_name="Apple Inc.",
        query="latest Apple news",
        period_start=date(2026, 6, 18),
        period_end=date(2026, 6, 18),
        search_provider=provider,
    )

    assert result.candidates == []
    assert result.stats.ranked_top_k == 0
    assert result.stats.with_raw_content == 0
