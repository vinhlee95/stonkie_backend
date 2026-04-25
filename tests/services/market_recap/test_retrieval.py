from datetime import UTC, date, datetime

from services.market_recap.query_planner import plan_queries
from services.market_recap.retrieval import retrieve_candidates
from services.market_recap.schemas import Candidate


class FakeSearchProvider:
    def __init__(self, payload_by_domain: dict[str, list[Candidate]]) -> None:
        self.payload_by_domain = payload_by_domain

    def search(
        self,
        query: str,
        period_start: date,
        period_end: date,
        include_domains: list[str] | None = None,
    ) -> list[Candidate]:
        domain_key = include_domains[0] if include_domains else "open"
        return self.payload_by_domain.get(domain_key, [])


def _candidate(
    *,
    title: str,
    url: str,
    score: float,
    raw_content: str,
    published_date: datetime,
) -> Candidate:
    return Candidate(
        title=title,
        url=url,
        snippet="snippet",
        published_date=published_date,
        raw_content=raw_content,
        score=score,
        provider="tavily",
    )


def test_retrieve_candidates_drops_empty_raw_content_before_ranking():
    provider = FakeSearchProvider(
        payload_by_domain={
            "open": [
                _candidate(
                    title="has content",
                    url="https://www.reuters.com/a",
                    score=0.8,
                    raw_content="body",
                    published_date=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
                ),
                _candidate(
                    title="no content",
                    url="https://www.reuters.com/b",
                    score=0.9,
                    raw_content="",
                    published_date=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
                ),
            ]
        }
    )

    result = retrieve_candidates(
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        search_provider=provider,
        planned_queries=plan_queries(date(2026, 4, 20), date(2026, 4, 24))[:1],
    )

    assert [candidate.title for candidate in result.candidates] == ["has content"]
    assert result.stats.results_total == 2
    assert result.stats.deduped == 2
    assert result.stats.with_raw_content == 1


def test_retrieve_candidates_returns_top_five_and_stats():
    base_time = datetime(2026, 4, 24, 16, 0, tzinfo=UTC)
    provider = FakeSearchProvider(
        payload_by_domain={
            "open": [
                _candidate(
                    title="open duplicate lower",
                    url="https://www.reuters.com/markets/shared?utm_source=x",
                    score=0.7,
                    raw_content="x",
                    published_date=base_time,
                ),
                _candidate(
                    title="open non allowlisted",
                    url="https://example.com/open",
                    score=0.99,
                    raw_content="x",
                    published_date=base_time,
                ),
            ],
            "reuters.com": [
                _candidate(
                    title="reuters duplicate higher",
                    url="https://www.reuters.com/markets/shared",
                    score=0.95,
                    raw_content="x",
                    published_date=base_time,
                ),
                _candidate(
                    title="reuters keep",
                    url="https://www.reuters.com/markets/keep",
                    score=0.8,
                    raw_content="x",
                    published_date=base_time,
                ),
            ],
            "apnews.com": [
                _candidate(
                    title="apnews keep",
                    url="https://apnews.com/article/one",
                    score=0.85,
                    raw_content="x",
                    published_date=base_time,
                ),
            ],
            "cnbc.com": [
                _candidate(
                    title="cnbc keep",
                    url="https://www.cnbc.com/2026/04/24/two.html",
                    score=0.82,
                    raw_content="x",
                    published_date=base_time,
                ),
            ],
            "marketwatch.com": [
                _candidate(
                    title="marketwatch drop no raw",
                    url="https://www.marketwatch.com/story/drop",
                    score=0.83,
                    raw_content="",
                    published_date=base_time,
                ),
            ],
            "sec.gov": [
                _candidate(
                    title="sec keep",
                    url="https://www.sec.gov/news/press-release",
                    score=0.60,
                    raw_content="x",
                    published_date=base_time,
                ),
            ],
            "federalreserve.gov": [
                _candidate(
                    title="fed keep but cut by top5",
                    url="https://www.federalreserve.gov/newsevents/a.htm",
                    score=0.50,
                    raw_content="x",
                    published_date=base_time,
                ),
            ],
        }
    )

    result = retrieve_candidates(
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        search_provider=provider,
        planned_queries=plan_queries(date(2026, 4, 20), date(2026, 4, 24)),
        top_k=5,
    )

    assert result.stats.queries_total == 7
    assert result.stats.results_total == 9
    assert result.stats.deduped == 8
    assert result.stats.with_raw_content == 7
    assert result.stats.allowlisted == 6
    assert result.stats.ranked_top_k == 5
    assert len(result.candidates) == 5
    assert all(candidate.raw_content for candidate in result.candidates)
    assert result.candidates[0].title == "reuters duplicate higher"
    assert result.candidates[-1].title != "open non allowlisted"
