from datetime import UTC, date, datetime

import pytest

from services.market_recap.criteria import MarketPolicy, policy_for
from services.market_recap.curation import CurationResult, CuratorError
from services.market_recap.query_planner import plan_queries
from services.market_recap.retrieval import retrieve_candidates
from services.market_recap.schemas import Candidate
from services.market_recap.source_policy import registrable_domain


class FakeSearchProvider:
    def __init__(
        self,
        payload_by_domain: dict[str, list[Candidate]],
        snapshot_by_domain: dict[str, dict] | None = None,
    ) -> None:
        self.payload_by_domain = payload_by_domain
        self.snapshot_by_domain = snapshot_by_domain or {}

    def search(
        self,
        query: str,
        period_start: date,
        period_end: date,
        include_domains: list[str] | None = None,
    ) -> list[Candidate]:
        domain_key = include_domains[0] if include_domains else "open"
        return self.payload_by_domain.get(domain_key, [])

    def search_with_snapshot(
        self,
        query: str,
        period_start: date,
        period_end: date,
        include_domains: list[str] | None = None,
    ) -> tuple[list[Candidate], dict]:
        domain_key = include_domains[0] if include_domains else "open"
        return self.payload_by_domain.get(domain_key, []), self.snapshot_by_domain.get(domain_key, {})


def _rules_only(*_args, **_kwargs):
    """A curator that always fails: the market falls back to rule selection (tests never call the LLM)."""
    raise CuratorError("disabled in test")


@pytest.fixture(autouse=True)
def _no_llm_curator(monkeypatch):
    """US, FI and VN all curate: without this, tests that don't inject `curate_fn` would call OpenRouter."""
    monkeypatch.setattr("services.market_recap.retrieval.curate_sources", _rules_only)


def _candidate(
    *,
    title: str,
    url: str,
    score: float,
    raw_content: str,
    published_date: datetime,
    provider: str = "tavily",
) -> Candidate:
    return Candidate(
        title=title,
        url=url,
        snippet="snippet",
        published_date=published_date,
        raw_content=raw_content,
        score=score,
        provider=provider,
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
        market="US",
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        search_provider=provider,
        planned_queries=plan_queries(date(2026, 4, 20), date(2026, 4, 24))[:1],
    )

    assert [candidate.title for candidate in result.candidates] == ["has content"]
    assert result.stats.results_total == 2
    assert result.stats.deduped == 2
    assert result.stats.with_raw_content == 1
    assert len(result.query_snapshots) == 1


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
                    title="reserve bank keep",
                    url="https://www.federalreserve.gov/newsevents/a.htm",
                    score=0.50,
                    raw_content="x",
                    published_date=base_time,
                ),
            ],
        }
    )

    result = retrieve_candidates(
        market="US",
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
    assert result.candidates[0].title in {"reuters duplicate higher", "apnews keep", "cnbc keep"}
    assert "reuters duplicate higher" in {candidate.title for candidate in result.candidates}
    assert result.candidates[-1].title != "open non allowlisted"
    assert len(result.query_snapshots) == 7


def test_retrieve_candidates_defaults_to_ten_diverse_sources():
    published = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
    publishers = (
        "apnews.com/article",
        "reuters.com/markets",
        "bloomberg.com/news",
        "nytimes.com/2026",
        "ft.com/content",
    )
    provider = FakeSearchProvider(
        payload_by_domain={
            "open": [
                _candidate(
                    title=f"Stocks story {index}",
                    url=f"https://www.{publisher}/story-{index}-{story}",
                    score=0.0,
                    raw_content="x" * 900,
                    published_date=published,
                    provider="brave",
                )
                for index, publisher in enumerate(publishers)
                for story in range(3)
            ]
        }
    )

    result = retrieve_candidates(
        market="US",
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        search_provider=provider,
        planned_queries=plan_queries(date(2026, 4, 20), date(2026, 4, 24))[:1],
    )

    assert result.stats.ranked_top_k == 10
    assert len(result.candidates) == 10
    assert {registrable_domain(candidate.url) for candidate in result.candidates} == {
        "apnews.com",
        "reuters.com",
        "bloomberg.com",
        "nytimes.com",
        "ft.com",
    }


def test_retrieve_candidates_prioritizes_allowlisted_for_vn_market():
    provider = FakeSearchProvider(
        payload_by_domain={
            "open": [
                _candidate(
                    title="non-vn domain",
                    url="https://example.com/world/china/a",
                    score=0.9,
                    raw_content="x",
                    published_date=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
                ),
                _candidate(
                    title="vn domain",
                    url="https://cafef.vn/thi-truong-chung-khoan.chn",
                    score=0.5,
                    raw_content="x",
                    published_date=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
                ),
            ]
        }
    )

    result = retrieve_candidates(
        market="VN",
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        search_provider=provider,
        planned_queries=plan_queries(date(2026, 4, 20), date(2026, 4, 24), market="VN")[:1],
        top_k=5,
        curate_fn=_rules_only,
    )

    assert [candidate.title for candidate in result.candidates][0] == "vn domain"


def test_retrieve_candidates_filters_out_of_window_for_brave():
    provider = FakeSearchProvider(
        payload_by_domain={
            "open": [
                _candidate(
                    title="in-window brave",
                    url="https://www.reuters.com/a",
                    score=0.9,
                    raw_content="x",
                    published_date=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
                    provider="brave",
                ),
                _candidate(
                    title="out-window brave",
                    url="https://www.reuters.com/b",
                    score=0.95,
                    raw_content="x",
                    published_date=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
                    provider="brave",
                ),
            ]
        }
    )
    result = retrieve_candidates(
        market="US",
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        search_provider=provider,
        planned_queries=[plan_queries(date(2026, 4, 20), date(2026, 4, 24))[0]],
        top_k=5,
    )
    assert [candidate.title for candidate in result.candidates] == ["in-window brave"]


def test_retrieve_candidates_collects_provider_snapshots():
    provider = FakeSearchProvider(
        payload_by_domain={
            "open": [
                _candidate(
                    title="snapshot candidate",
                    url="https://www.reuters.com/a",
                    score=0.8,
                    raw_content="x",
                    published_date=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
                )
            ]
        },
        snapshot_by_domain={"open": {"request_id": "abc-123", "shape": "grounding.generic"}},
    )
    result = retrieve_candidates(
        market="US",
        period_start=date(2026, 4, 24),
        period_end=date(2026, 4, 24),
        search_provider=provider,
        planned_queries=[plan_queries(date(2026, 4, 24), date(2026, 4, 24), cadence="daily")[0]],
    )
    assert len(result.query_snapshots) == 1
    assert result.query_snapshots[0]["query"] == "US stock market recap Apr 24, 2026"
    assert result.query_snapshots[0]["provider_snapshot"] == {"request_id": "abc-123", "shape": "grounding.generic"}


@pytest.mark.parametrize("market", ["US", "VN"])
def test_retrieve_candidates_default_provider_routing(monkeypatch, market):
    captured = {}

    class _Provider:
        def search(self, **kwargs):
            return []

    class _BraveStub(_Provider):
        def __init__(self, api_key, market="VN"):
            captured["provider"] = self
            captured["api_key"] = api_key
            captured["market"] = market

    monkeypatch.setenv("BRAVE_API_KEY", "b-key")
    monkeypatch.setattr("services.market_recap.retrieval.BraveClient", _BraveStub)

    result = retrieve_candidates(
        market=market,
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
    )
    assert isinstance(captured["provider"], _BraveStub)
    assert captured["market"] == market
    assert result.stats.results_total == 0


# --- LLM curator wiring ----------------------------------------------------------------------------------------


def _curator_pool_provider() -> FakeSearchProvider:
    published = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    return FakeSearchProvider(
        payload_by_domain={
            "open": [
                _candidate(
                    title="Helsinki rises",
                    url="https://inderes.fi/news/a",
                    score=0.0,
                    raw_content="x",
                    published_date=published,
                ),
                _candidate(
                    title="Not allowlisted",
                    url="https://example.com/news/b",
                    score=0.0,
                    raw_content="x",
                    published_date=published,
                ),
                _candidate(
                    title="Helsinki falls",
                    url="https://www.reuters.com/markets/c",
                    score=0.0,
                    raw_content="x",
                    published_date=published,
                ),
            ]
        }
    )


def _retrieve_curated(monkeypatch, curate_fn, *, curated: bool = True):
    monkeypatch.setattr(
        "services.market_recap.retrieval.policy_for",
        lambda market: MarketPolicy(curated_selection=curated, relevance_hints=policy_for("FI").relevance_hints),
    )
    return retrieve_candidates(
        market="FI",
        period_start=date(2026, 11, 2),
        period_end=date(2026, 11, 6),
        search_provider=_curator_pool_provider(),
        planned_queries=[plan_queries(date(2026, 11, 2), date(2026, 11, 6), market="FI")[0]],
        curate_fn=curate_fn,
    )


def test_curated_market_hands_the_allowlisted_pool_to_the_curator_and_uses_its_picks(monkeypatch):
    calls = []

    def fake_curate(pool, **kwargs):
        calls.append(([c.title for c in pool], kwargs))
        return CurationResult(candidates=[pool[1]], audit={"method": "curator", "picks": ["S2"]})

    result = _retrieve_curated(monkeypatch, fake_curate)

    [(titles, kwargs)] = calls
    assert titles == ["Helsinki rises", "Helsinki falls"]
    assert kwargs == {
        "market": "FI",
        "cadence": "weekly",
        "period_start": date(2026, 11, 2),
        "period_end": date(2026, 11, 6),
        "top_k": 10,
    }
    assert [c.title for c in result.candidates] == ["Helsinki falls"]
    assert result.selection_audit == {"method": "curator", "picks": ["S2"]}


def test_curator_failure_falls_back_to_rule_selection(monkeypatch, caplog):
    def failing_curate(pool, **kwargs):
        raise CuratorError("too_few_picks: 2")

    with caplog.at_level("INFO"):
        result = _retrieve_curated(monkeypatch, failing_curate)

    assert {c.title for c in result.candidates} == {"Helsinki rises", "Helsinki falls", "Not allowlisted"}
    assert result.selection_audit == {"method": "rules_fallback", "fallback_reason": "too_few_picks: 2"}
    [fallback] = [r for r in caplog.records if getattr(r, "event", None) == "recap.curator.fallback"]
    assert fallback.fields == {"market": "FI", "reason": "too_few_picks: 2"}


def test_market_without_the_curator_never_calls_it(monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("curator called")

    result = _retrieve_curated(monkeypatch, unexpected, curated=False)

    assert result.selection_audit == {"method": "rules"}
    assert len(result.candidates) == 3


def test_curator_is_on_for_us_fi_and_vn():
    assert policy_for("US").curated_selection is True
    assert policy_for("FI").curated_selection is True
    assert policy_for("VN").curated_selection is True
    assert policy_for("XX").curated_selection is False


def test_curated_market_without_an_injected_curator_uses_the_module_default(monkeypatch):
    calls = []

    def fake_default(pool, **kwargs):
        calls.append(len(pool))
        return CurationResult(candidates=pool[:1], audit={"method": "curator"})

    monkeypatch.setattr("services.market_recap.retrieval.curate_sources", fake_default)
    result = _retrieve_curated(monkeypatch, None)

    assert calls == [2]
    assert result.selection_audit == {"method": "curator"}
