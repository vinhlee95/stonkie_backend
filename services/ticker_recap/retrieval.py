from datetime import date

from services.market_recap.ranking import dedupe
from services.market_recap.retrieval import _filter_brave_out_of_window, _provider_for
from services.market_recap.schemas import RetrievalResult, RetrievalStats
from services.market_recap.search_client import SearchProvider
from services.market_recap.source_policy import is_allowlisted
from services.ticker_recap.ranking import rank


def retrieve_for_ticker(
    ticker: str,
    company_name: str,
    query: str,
    period_start: date,
    period_end: date,
    market: str = "US",
    search_provider: SearchProvider | None = None,
    top_k: int = 5,
) -> RetrievalResult:
    provider = search_provider or _provider_for(market)

    search_with_snapshot = getattr(provider, "search_with_snapshot", None)
    if callable(search_with_snapshot):
        fetched_candidates, provider_snapshot = search_with_snapshot(
            query=query,
            period_start=period_start,
            period_end=period_end,
            include_domains=None,
        )
    else:
        fetched_candidates = provider.search(
            query=query,
            period_start=period_start,
            period_end=period_end,
            include_domains=None,
        )
        provider_snapshot = None

    query_snapshots = [
        {
            "query": query,
            "include_domains": [],
            "results_count": len(fetched_candidates),
            "provider_snapshot": provider_snapshot,
        }
    ]

    deduped = dedupe(fetched_candidates)
    with_raw_content = [candidate for candidate in deduped if candidate.raw_content.strip()]
    with_raw_content = _filter_brave_out_of_window(with_raw_content, period_start, period_end)
    allowlisted_count = len([c for c in with_raw_content if is_allowlisted(c.url, market=market)])
    ranked = rank(with_raw_content, ticker=ticker, company_name=company_name, market=market)
    top_candidates = ranked[:top_k]

    return RetrievalResult(
        candidates=top_candidates,
        stats=RetrievalStats(
            queries_total=1,
            results_total=len(fetched_candidates),
            deduped=len(deduped),
            with_raw_content=len(with_raw_content),
            allowlisted=allowlisted_count,
            ranked_top_k=len(top_candidates),
        ),
        query_snapshots=query_snapshots,
    )
