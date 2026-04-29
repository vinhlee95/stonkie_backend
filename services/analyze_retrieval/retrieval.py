from __future__ import annotations

from typing import Protocol

from services.analyze_retrieval.goggle import build_chat_goggle
from services.analyze_retrieval.observability import log_retrieval
from services.analyze_retrieval.publisher import publisher_label_for
from services.analyze_retrieval.ranking import rank_for_chat
from services.analyze_retrieval.schemas import AnalyzeRetrievalResult, AnalyzeSource, BraveRetrievalError
from services.analyze_retrieval.source_policy import Market, is_trusted
from services.market_recap.schemas import Candidate
from services.market_recap.url_utils import source_id_for


class BraveSearchClient(Protocol):
    def search(
        self,
        *,
        query: str,
        country: str,
        search_lang: str,
        goggle: str,
        count: int = 20,
    ) -> list[Candidate]: ...


def _country_and_lang_for(market: Market) -> tuple[str, str]:
    if market == "VN":
        return ("ALL", "vi")
    if market == "FI":
        return ("FI", "en")
    return ("US", "en")


def retrieve_for_analyze(
    *,
    question: str,
    market: Market,
    request_id: str,
    brave_client: BraveSearchClient,
    ticker: str = "UNKNOWN",
    brave_latency_ms: int = 0,
    top_k: int = 5,
) -> AnalyzeRetrievalResult:
    country, search_lang = _country_and_lang_for(market)
    candidates = brave_client.search(
        query=question,
        country=country,
        search_lang=search_lang,
        goggle=build_chat_goggle(market),
        count=20,
    )

    best_candidate_by_canonical_url: dict[str, Candidate] = {}
    for candidate in candidates:
        if not candidate.raw_content.strip():
            continue
        canonical_url = candidate.canonical_url
        current_best = best_candidate_by_canonical_url.get(canonical_url)
        if current_best is None:
            best_candidate_by_canonical_url[canonical_url] = candidate
            continue

        if (candidate.score, len(candidate.raw_content)) > (
            current_best.score,
            len(current_best.raw_content),
        ):
            best_candidate_by_canonical_url[canonical_url] = candidate

    unique_candidates = list(best_candidate_by_canonical_url.values())

    ranked_candidates = rank_for_chat(unique_candidates, market=market)
    selected_candidates = ranked_candidates[:top_k]
    if not selected_candidates:
        raise BraveRetrievalError("No Brave results available after filtering")

    sources = [
        AnalyzeSource(
            id=source_id_for(candidate.url),
            url=candidate.url,
            title=candidate.title,
            publisher=publisher_label_for(candidate.url),
            published_at=candidate.published_date,
            is_trusted=is_trusted(candidate.url, market),
        )
        for candidate in selected_candidates
    ]

    log_retrieval(
        request_id=request_id,
        ticker=ticker,
        market=market,
        ranked_urls=[candidate.url for candidate in ranked_candidates],
        selected_source_ids=[source.id for source in sources],
        brave_latency_ms=brave_latency_ms,
        raw_brave_response=None,
    )

    return AnalyzeRetrievalResult(
        sources=sources,
        query=question,
        market=market,
        request_id=request_id,
    )
