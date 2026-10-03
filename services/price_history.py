"""5y of completed-session daily closes per Yahoo symbol, cached in Redis."""

import logging
import math
from datetime import UTC, date, datetime

from connectors import cache
from connectors.yfinance_client import YFinanceClient

logger = logging.getLogger(__name__)

# Closes only change once per session; dropping today's bar (below) keeps a cached entry from going stale mid-session.
PRICE_HISTORY_TTL_SECONDS = 12 * 3600


def _utcnow() -> datetime:
    return datetime.now(UTC)


def get_close_histories(symbols: list[str], yf_client: YFinanceClient) -> dict[str, dict[str, float]]:
    """Daily closes per symbol as {ISO date: close}, oldest first. Cache misses are fetched in one
    batched download. Symbols without usable history (or a failed download) are omitted."""
    symbols = list(dict.fromkeys(symbols))
    histories: dict[str, dict[str, float]] = {}
    misses = []
    for symbol, cached in zip(symbols, cache.get_json_many([_cache_key(s) for s in symbols])):
        closes = _from_cache(symbol, cached)
        if closes is not None:
            histories[symbol] = closes
        else:
            misses.append(symbol)
    if not misses:
        return histories

    try:
        fetched = yf_client.get_close_history_batch(misses)
    except Exception:
        logger.warning("Failed to fetch price history for %s", misses, exc_info=True)
        return histories

    # Bars are dated in exchange-local time; anything dated today (UTC) or later may still be trading.
    today = _utcnow().date()
    for symbol in misses:
        series = fetched.get(symbol)
        closes = {}
        if series is not None:
            closes = {
                ts.date().isoformat(): float(v) for ts, v in series.items() if ts.date() < today and _is_positive(v)
            }
        if not closes:
            logger.info("No price history for %s", symbol)
            continue
        histories[symbol] = closes
        cache.set_json(_cache_key(symbol), {"closes": closes}, PRICE_HISTORY_TTL_SECONDS)
    return histories


def _cache_key(symbol: str) -> str:
    return f"price_history:{symbol}:5y"


def _from_cache(symbol: str, cached: dict | None) -> dict[str, float] | None:
    if cached is None:
        return None
    closes = cached.get("closes") if isinstance(cached, dict) else None
    try:
        if not isinstance(closes, dict) or not closes:
            raise ValueError("missing closes")
        for day, close in closes.items():
            date.fromisoformat(day)
            if not _is_positive(close):
                raise ValueError("non-positive or non-numeric close")
    except (TypeError, ValueError):
        logger.warning("Invalid cached price history for %s", symbol)
        return None
    return closes


def _is_positive(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
