from __future__ import annotations

from typing import Protocol

from services.analyze_retrieval.goggle import build_chat_goggle
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

    unique_candidates: list[Candidate] = []
    seen_canonical_urls: set[str] = set()
    for candidate in candidates:
        if not candidate.raw_content.strip():
            continue
        canonical_url = candidate.canonical_url
        if canonical_url in seen_canonical_urls:
            continue
        seen_canonical_urls.add(canonical_url)
        unique_candidates.append(candidate)

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

    return AnalyzeRetrievalResult(
        sources=sources,
        query=question,
        market=market,
        request_id=request_id,
    )
