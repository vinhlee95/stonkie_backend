from __future__ import annotations

import re
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


def _clean_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _rewrite_company_question(question: str, *, company_name: str) -> str:
    rewritten = question
    for pattern in (
        r"\bits\b",
        r"\bit\b",
        r"\bthis company\b",
        r"\bthe company\b",
        r"\bthis stock\b",
        r"\bthe business\b",
    ):
        rewritten = re.sub(pattern, company_name, rewritten, flags=re.IGNORECASE)
    return _clean_whitespace(rewritten)


def build_company_aware_query(question: str, *, ticker: str | None = None, company_name: str | None = None) -> str:
    base_question = _clean_whitespace(question)
    if not company_name:
        return base_question

    rewritten_question = _rewrite_company_question(base_question, company_name=company_name)
    ticker_token = (ticker or "").strip().upper()
    return _clean_whitespace(
        " ".join(part for part in (company_name.strip(), ticker_token, rewritten_question) if part)
    )


def retrieve_for_analyze(
    *,
    question: str,
    market: Market,
    request_id: str,
    brave_client: BraveSearchClient,
    ticker: str = "UNKNOWN",
    company_name: str | None = None,
    brave_latency_ms: int = 0,
    top_k: int = 5,
) -> AnalyzeRetrievalResult:
    country, search_lang = _country_and_lang_for(market)
    brave_query = build_company_aware_query(question, ticker=ticker, company_name=company_name)
    candidates = brave_client.search(
        query=brave_query,
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
            raw_content=candidate.raw_content,
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
        query=brave_query,
        market=market,
        request_id=request_id,
    )
