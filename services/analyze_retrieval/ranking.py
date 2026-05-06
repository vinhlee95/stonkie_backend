from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeSource
from services.analyze_retrieval.source_policy import Market, registrable_domain, tier_for
from services.market_recap.schemas import Candidate

_RECENCY_WINDOW_DAYS = 90
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "apple",
    "apples",
    "are",
    "for",
    "how",
    "in",
    "is",
    "it",
    "its",
    "latest",
    "of",
    "on",
    "or",
    "the",
    "to",
    "was",
    "what",
}


def _is_recent(published_date: datetime | None, now: datetime) -> bool:
    if published_date is None:
        return False
    normalized = published_date
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=UTC)
    else:
        normalized = normalized.astimezone(UTC)
    return normalized >= now - timedelta(days=_RECENCY_WINDOW_DAYS)


def rank_for_chat(candidates: list[Candidate], market: Market) -> list[Candidate]:
    if not candidates:
        return []

    now = datetime.now(UTC)

    def sort_key(item: Candidate) -> tuple[int, int, int, float, str]:
        tier = tier_for(item.url, market)
        tier_order = {1: 0, 2: 1, None: 2}[tier]
        recency_order = 0 if _is_recent(item.published_date, now) else 1
        content_len_order = -len(item.raw_content)
        score_order = -item.score
        return (tier_order, recency_order, content_len_order, score_order, item.url)

    return sorted(candidates, key=sort_key)


def split_source_into_passages(source: AnalyzeSource) -> list[AnalyzePassage]:
    passages: list[AnalyzePassage] = []
    raw_content = (source.raw_content or "").strip()
    if not raw_content:
        return passages

    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n+", raw_content) if chunk.strip()]
    for index, chunk in enumerate(chunks):
        passages.append(
            AnalyzePassage(
                source_id=source.id,
                url=source.url,
                title=source.title,
                publisher=source.publisher,
                published_at=source.published_at,
                is_trusted=source.is_trusted,
                passage_index=index,
                content=chunk,
            )
        )
    return passages


def rank_passages_for_chat(
    *,
    question: str,
    sources: list[AnalyzeSource],
    max_passages: int = 10,
    max_sources: int = 5,
    max_passages_per_source: int = 2,
    max_sources_per_domain: int = 2,
) -> list[AnalyzePassage]:
    if not sources:
        return []

    query_terms = _meaningful_terms(question)

    source_order = {source.id: index for index, source in enumerate(sources)}
    scored: list[tuple[tuple[int, int, int, int, str, int], AnalyzePassage]] = []
    for source in sources:
        for passage in split_source_into_passages(source):
            scored.append((_passage_sort_key(passage, query_terms, source_order), passage))

    selected: list[AnalyzePassage] = []
    per_source_counts: dict[str, int] = {}
    per_domain_source_ids: dict[str, set[str]] = {}
    selected_source_ids: set[str] = set()

    for _, passage in sorted(scored, key=lambda item: item[0]):
        if len(selected) >= max_passages:
            break
        if passage.source_id not in selected_source_ids and len(selected_source_ids) >= max_sources:
            continue
        if per_source_counts.get(passage.source_id, 0) >= max_passages_per_source:
            continue
        domain = registrable_domain(passage.url)
        domain_source_ids = per_domain_source_ids.setdefault(domain, set())
        if passage.source_id not in domain_source_ids and len(domain_source_ids) >= max_sources_per_domain:
            continue

        selected.append(passage)
        per_source_counts[passage.source_id] = per_source_counts.get(passage.source_id, 0) + 1
        domain_source_ids.add(passage.source_id)
        selected_source_ids.add(passage.source_id)

    return selected


def _meaningful_terms(text: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(text.lower()) if token not in _STOP_WORDS}


def _passage_sort_key(
    passage: AnalyzePassage,
    query_terms: set[str],
    source_order: dict[str, int],
) -> tuple[int, int, int, int, str, int]:
    passage_terms = _meaningful_terms(passage.content)
    overlap = len(query_terms & passage_terms)
    trusted_order = 0 if passage.is_trusted else 1
    content_len_order = -len(passage.content)
    source_rank = source_order.get(passage.source_id, 0)
    return (-overlap, trusted_order, source_rank, content_len_order, passage.url, passage.passage_index)
