from datetime import date

from services.market_recap.query_planner import plan_queries
from services.market_recap.ranking import dedupe, rank
from services.market_recap.schemas import PlannedQuery, RetrievalResult, RetrievalStats
from services.market_recap.search_client import SearchProvider
from services.market_recap.source_policy import is_allowlisted


def retrieve_candidates(
    period_start: date,
    period_end: date,
    search_provider: SearchProvider,
    planned_queries: list[PlannedQuery] | None = None,
    top_k: int = 5,
) -> RetrievalResult:
    queries = planned_queries if planned_queries is not None else plan_queries(period_start, period_end)
    fetched_candidates = []
    for planned_query in queries:
        fetched_candidates.extend(
            search_provider.search(
                query=planned_query.query,
                period_start=period_start,
                period_end=period_end,
                include_domains=planned_query.include_domains,
            )
        )

    deduped = dedupe(fetched_candidates)
    with_raw_content = [candidate for candidate in deduped if candidate.raw_content.strip()]
    allowlisted_count = sum(1 for candidate in with_raw_content if is_allowlisted(candidate.url))
    ranked = rank(with_raw_content)
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
