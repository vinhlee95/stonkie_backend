import os
from datetime import date, timedelta

from services.market_recap.brave_client import BraveClient
from services.market_recap.query_planner import plan_queries
from services.market_recap.ranking import dedupe, rank
from services.market_recap.schemas import Candidate, PlannedQuery, RetrievalResult, RetrievalStats
from services.market_recap.search_client import SearchProvider
from services.market_recap.source_policy import is_allowlisted
from services.market_recap.tavily_client import TavilyClient


def _provider_for(market: str) -> SearchProvider:
    market_key = market.upper()
    if market_key == "VN":
        api_key = os.getenv("BRAVE_API_KEY")
        if not api_key:
            raise RuntimeError("BRAVE_API_KEY is required for VN retrieval")
        return BraveClient(api_key=api_key, market=market_key)
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is required for US retrieval")
    return TavilyClient(api_key=api_key)


def _in_window(value: date, start: date, end: date, grace_days: int = 1) -> bool:
    floor = start - timedelta(days=grace_days)
    ceiling = end + timedelta(days=grace_days)
    return floor <= value <= ceiling


def _filter_brave_out_of_window(candidates: list[Candidate], period_start: date, period_end: date) -> list[Candidate]:
    brave_candidates = [candidate for candidate in candidates if candidate.provider == "brave"]
    if not brave_candidates:
        return candidates

    in_window = [
        candidate
        for candidate in candidates
        if candidate.provider != "brave"
        or (
            candidate.published_date is not None
            and _in_window(candidate.published_date.date(), period_start, period_end)
        )
    ]
    return in_window


def retrieve_candidates(
    market: str,
    period_start: date,
    period_end: date,
    search_provider: SearchProvider | None = None,
    planned_queries: list[PlannedQuery] | None = None,
    top_k: int = 5,
    cadence: str = "weekly",
) -> RetrievalResult:
    provider = search_provider or _provider_for(market)
    queries = (
        planned_queries
        if planned_queries is not None
        else plan_queries(period_start, period_end, market=market, cadence=cadence)
    )
    fetched_candidates = []
    for planned_query in queries:
        fetched_candidates.extend(
            provider.search(
                query=planned_query.query,
                period_start=period_start,
                period_end=period_end,
                include_domains=planned_query.include_domains,
            )
        )

    deduped = dedupe(fetched_candidates)
    with_raw_content = [candidate for candidate in deduped if candidate.raw_content.strip()]
    with_raw_content = _filter_brave_out_of_window(with_raw_content, period_start, period_end)
    allowlisted = [candidate for candidate in with_raw_content if is_allowlisted(candidate.url, market=market)]
    allowlisted_count = len(allowlisted)
    ranked = rank(with_raw_content, market=market)
    top_candidates = ranked[:top_k]

    return RetrievalResult(
        candidates=top_candidates,
        stats=RetrievalStats(
            queries_total=len(queries),
            results_total=len(fetched_candidates),
            deduped=len(deduped),
            with_raw_content=len(with_raw_content),
            allowlisted=allowlisted_count,
            ranked_top_k=len(top_candidates),
        ),
    )
