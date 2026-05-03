from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from services.analyze_retrieval.freshness import (
    FreshnessPolicy,
    freshness_for_question,
    is_within_freshness_window,
)
from services.analyze_retrieval.goggle import build_chat_goggle
from services.analyze_retrieval.observability import log_retrieval
from services.analyze_retrieval.publisher import publisher_label_for
from services.analyze_retrieval.schemas import AnalyzeRetrievalResult, AnalyzeSource, BraveRetrievalError
from services.analyze_retrieval.source_policy import Market, is_trusted, registrable_domain
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
        freshness: str | None = None,
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
    freshness_policy = freshness_for_question(question)
    candidate_count = _candidate_count_for(freshness_policy)
    candidates = brave_client.search(
        query=question,
        country=country,
        search_lang=search_lang,
        goggle=build_chat_goggle(market),
        count=candidate_count,
        freshness=freshness_policy.value if freshness_policy is not None else None,
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
        if candidate.url == canonical_url and current_best.url != canonical_url:
            best_candidate_by_canonical_url[canonical_url] = candidate

    unique_candidates = list(best_candidate_by_canonical_url.values())
    freshness_filtered_candidates, stale_dropped = _filter_stale_candidates(
        unique_candidates,
        freshness_policy=freshness_policy,
    )
    selected_candidates, selection_stats = _select_candidates(
        freshness_filtered_candidates,
        market=market,
        top_k=top_k,
    )
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

    selected_source_ages = [_age_bucket(candidate.published_date) for candidate in selected_candidates]

    log_retrieval(
        request_id=request_id,
        ticker=ticker,
        market=market,
        ranked_urls=[candidate.url for candidate in freshness_filtered_candidates],
        selected_source_ids=[source.id for source in sources],
        brave_latency_ms=brave_latency_ms,
        freshness=freshness_policy.value if freshness_policy is not None else None,
        returned_candidates=len(candidates),
        unique_candidates=len(unique_candidates),
        unique_domains=selection_stats["unique_domains"],
        selected_domains=selection_stats["selected_domains"],
        selected_source_ages=selected_source_ages,
        stale_dropped=stale_dropped,
        trusted_selected=selection_stats["trusted_selected"],
        used_untrusted_backfill=selection_stats["used_untrusted_backfill"],
        raw_brave_response=None,
    )

    return AnalyzeRetrievalResult(
        sources=sources,
        query=question,
        market=market,
        request_id=request_id,
    )


def _filter_stale_candidates(
    candidates: list[Candidate],
    *,
    freshness_policy: FreshnessPolicy | None,
) -> tuple[list[Candidate], int]:
    if freshness_policy is None:
        return candidates, 0

    filtered: list[Candidate] = []
    stale_dropped = 0
    for candidate in candidates:
        if is_within_freshness_window(candidate.published_date, policy=freshness_policy):
            filtered.append(candidate)
        elif candidate.published_date is not None:
            stale_dropped += 1
    return filtered, stale_dropped


def _select_candidates(
    candidates: list[Candidate],
    *,
    market: Market,
    top_k: int,
) -> tuple[list[Candidate], dict[str, object]]:
    trusted_candidates = [candidate for candidate in candidates if is_trusted(candidate.url, market)]
    untrusted_candidates = [candidate for candidate in candidates if not is_trusted(candidate.url, market)]

    selected_trusted = _pick_with_domain_cap(trusted_candidates, limit=top_k)
    selected_domains = {registrable_domain(candidate.url) for candidate in selected_trusted}

    remaining_slots = top_k - len(selected_trusted)
    used_untrusted_backfill = False
    if remaining_slots > 0:
        selected_untrusted = _pick_with_domain_cap(
            untrusted_candidates,
            limit=remaining_slots,
            seen_domains=selected_domains,
        )
        if selected_untrusted:
            used_untrusted_backfill = True
            selected_domains.update(registrable_domain(candidate.url) for candidate in selected_untrusted)
            selected_candidates = selected_trusted + selected_untrusted
        else:
            selected_candidates = selected_trusted
    else:
        selected_candidates = selected_trusted

    if len(selected_candidates) < top_k:
        fallback_pool = [candidate for candidate in candidates if candidate not in selected_candidates]
        selected_candidates.extend(
            _backfill_balancing_domains(
                fallback_pool,
                limit=top_k - len(selected_candidates),
                already_selected=selected_candidates,
            )
        )

    return selected_candidates, {
        "unique_domains": len({registrable_domain(candidate.url) for candidate in candidates if candidate.url}),
        "selected_domains": [registrable_domain(candidate.url) for candidate in selected_candidates],
        "trusted_selected": len([candidate for candidate in selected_candidates if is_trusted(candidate.url, market)]),
        "used_untrusted_backfill": used_untrusted_backfill,
    }


def _pick_with_domain_cap(
    candidates: list[Candidate],
    *,
    limit: int,
    seen_domains: set[str] | None = None,
) -> list[Candidate]:
    selected: list[Candidate] = []
    domains = set() if seen_domains is None else set(seen_domains)

    for candidate in candidates:
        if len(selected) >= limit:
            break
        domain = registrable_domain(candidate.url)
        if domain in domains:
            continue
        selected.append(candidate)
        domains.add(domain)

    if len(selected) < limit:
        selected.extend(
            _backfill_balancing_domains(
                [candidate for candidate in candidates if candidate not in selected],
                limit=limit - len(selected),
                already_selected=selected,
            )
        )
    return selected


def _age_bucket(published_at: datetime | None) -> str | None:
    if published_at is None:
        return None
    now = datetime.now(UTC)
    normalized = published_at.astimezone(UTC) if published_at.tzinfo else published_at.replace(tzinfo=UTC)
    age_days = max(0, (now - normalized).days)
    if age_days <= 7:
        return "pw"
    if age_days <= 31:
        return "pm"
    if age_days <= 365:
        return "py"
    return "old"


def _candidate_count_for(freshness_policy: FreshnessPolicy | None) -> int:
    if freshness_policy is None:
        return 20
    if freshness_policy.value == "pw":
        return 50
    return 30


def _backfill_balancing_domains(
    candidates: list[Candidate],
    *,
    limit: int,
    already_selected: list[Candidate],
) -> list[Candidate]:
    if limit <= 0 or not candidates:
        return []

    domain_counts: dict[str, int] = {}
    for candidate in already_selected:
        domain = registrable_domain(candidate.url)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1

    enumerated = list(enumerate(candidates))
    selected: list[Candidate] = []
    selected_indexes: set[int] = set()

    while len(selected) < limit and len(selected_indexes) < len(enumerated):
        best_index: int | None = None
        best_key: tuple[int, int] | None = None
        for original_index, candidate in enumerated:
            if original_index in selected_indexes:
                continue
            domain = registrable_domain(candidate.url)
            key = (domain_counts.get(domain, 0), original_index)
            if best_key is None or key < best_key:
                best_key = key
                best_index = original_index
        if best_index is None:
            break
        selected_indexes.add(best_index)
        candidate = candidates[best_index]
        selected.append(candidate)
        domain = registrable_domain(candidate.url)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1

    return selected
