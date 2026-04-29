from __future__ import annotations

from datetime import UTC, datetime, timedelta

from services.analyze_retrieval.source_policy import Market, tier_for
from services.market_recap.schemas import Candidate

_RECENCY_WINDOW_DAYS = 90


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
