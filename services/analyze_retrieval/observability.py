from __future__ import annotations

import json
import logging

logger = logging.getLogger("app.analyze_retrieval")


def log_retrieval(
    *,
    request_id: str,
    ticker: str,
    market: str,
    ranked_urls: list[str],
    selected_source_ids: list[str],
    brave_latency_ms: int,
    freshness: str | None = None,
    returned_candidates: int | None = None,
    unique_candidates: int | None = None,
    unique_domains: int | None = None,
    selected_domains: list[str] | None = None,
    selected_source_ages: list[str | None] | None = None,
    stale_dropped: int | None = None,
    trusted_selected: int | None = None,
    used_untrusted_backfill: bool | None = None,
    raw_brave_response: dict | None = None,
) -> None:
    _ = raw_brave_response
    payload = {
        "request_id": request_id,
        "ticker": ticker,
        "market": market,
        "ranked_urls": ranked_urls,
        "selected_source_ids": selected_source_ids,
        "brave_latency_ms": brave_latency_ms,
    }
    if freshness is not None:
        payload["freshness"] = freshness
    if returned_candidates is not None:
        payload["returned_candidates"] = returned_candidates
    if unique_candidates is not None:
        payload["unique_candidates"] = unique_candidates
    if unique_domains is not None:
        payload["unique_domains"] = unique_domains
    if selected_domains is not None:
        payload["selected_domains"] = selected_domains
    if selected_source_ages is not None:
        payload["selected_source_ages"] = selected_source_ages
    if stale_dropped is not None:
        payload["stale_dropped"] = stale_dropped
    if trusted_selected is not None:
        payload["trusted_selected"] = trusted_selected
    if used_untrusted_backfill is not None:
        payload["used_untrusted_backfill"] = used_untrusted_backfill
    logger.info(json.dumps(payload, sort_keys=True))
