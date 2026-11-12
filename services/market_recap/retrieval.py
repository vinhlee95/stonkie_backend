import logging
import os
from datetime import date, timedelta
from typing import Callable

from services.market_recap.brave_client import BraveClient
from services.market_recap.criteria import policy_for
from services.market_recap.curation import CuratorError, curate_sources
from services.market_recap.logging import log_event
from services.market_recap.query_planner import plan_queries
from services.market_recap.ranking import dedupe, rank
from services.market_recap.schemas import Candidate, PlannedQuery, RetrievalResult, RetrievalStats
from services.market_recap.search_client import SearchProvider
from services.market_recap.selection import select_candidates
from services.market_recap.source_policy import is_allowlisted

logger = logging.getLogger(__name__)

EVENT_CURATOR_FALLBACK = "recap.curator.fallback"


def _provider_for(market: str) -> SearchProvider:
    market_key = market.upper()
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key:
        raise RuntimeError("BRAVE_API_KEY is required for retrieval")
    return BraveClient(api_key=api_key, market=market_key)


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
    top_k: int = 10,
    cadence: str = "weekly",
    curate_fn: Callable | None = None,
) -> RetrievalResult:
    provider = search_provider or _provider_for(market)
    queries = (
        planned_queries
        if planned_queries is not None
        else plan_queries(period_start, period_end, market=market, cadence=cadence)
    )
    fetched_candidates = []
    query_snapshots: list[dict] = []
    for planned_query in queries:
        search_with_snapshot = getattr(provider, "search_with_snapshot", None)
        if callable(search_with_snapshot):
            candidates, provider_snapshot = search_with_snapshot(
                query=planned_query.query,
                period_start=period_start,
                period_end=period_end,
                include_domains=planned_query.include_domains,
            )
        else:
            candidates = provider.search(
                query=planned_query.query,
                period_start=period_start,
                period_end=period_end,
                include_domains=planned_query.include_domains,
            )
            provider_snapshot = None
        fetched_candidates.extend(candidates)
        query_snapshots.append(
            {
                "query": planned_query.query,
                "include_domains": planned_query.include_domains,
                "results_count": len(candidates),
                "provider_snapshot": provider_snapshot,
            }
        )

    deduped = dedupe(fetched_candidates)
    with_raw_content = [candidate for candidate in deduped if candidate.raw_content.strip()]
    with_raw_content = _filter_brave_out_of_window(with_raw_content, period_start, period_end)
    allowlisted = [candidate for candidate in with_raw_content if is_allowlisted(candidate.url, market=market)]
    allowlisted_count = len(allowlisted)
    ranked = rank(with_raw_content, market=market)

    def rule_selection() -> list[Candidate]:
        return select_candidates(ranked, market=market, period_start=period_start, period_end=period_end, top_k=top_k)

    selection_audit: dict = {"method": "rules"}
    if policy_for(market).curated_selection:
        try:
            curated = (curate_fn or curate_sources)(
                allowlisted,
                market=market,
                cadence=cadence,
                period_start=period_start,
                period_end=period_end,
                top_k=top_k,
            )
            top_candidates, selection_audit = curated.candidates, curated.audit
        except CuratorError as error:
            log_event(logger, EVENT_CURATOR_FALLBACK, {"market": market.upper(), "reason": str(error)})
            top_candidates = rule_selection()
            selection_audit = {"method": "rules_fallback", "fallback_reason": str(error)}
    else:
        top_candidates = rule_selection()

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
        query_snapshots=query_snapshots,
        selection_audit=selection_audit,
    )
