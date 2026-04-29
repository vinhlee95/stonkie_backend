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
    logger.info(json.dumps(payload, sort_keys=True))
