"""5y of completed-session daily closes per Yahoo symbol, cached in Redis."""

import logging
import math
from datetime import UTC, date, datetime

from connectors import cache
from connectors.yfinance_client import YFinanceClient

logger = logging.getLogger(__name__)

# Entries are keyed by UTC date, so every symbol in a request shares one "completed sessions" cutoff
# and a holding's newest close never sits next to a benchmark cached before that session closed.
PRICE_HISTORY_TTL_SECONDS = 24 * 3600
# Symbols Yahoo has no prices for are remembered briefly so each dashboard load doesn't re-download
# them. Failed fetches are never cached this way; kept short anyway since a shared ^GSPC / FX key
# marked empty blanks every user's chart.
NO_HISTORY_TTL_SECONDS = 300


def _utcnow() -> datetime:
    return datetime.now(UTC)


def get_close_histories(symbols: list[str], yf_client: YFinanceClient) -> dict[str, dict[str, float]]:
    """Daily closes per symbol as {ISO date: close}, oldest first. Cache misses are fetched in one
    batched download. Symbols without usable history (or a failed download) are omitted."""
    symbols = list(dict.fromkeys(symbols))
    # Bars are dated in exchange-local time; anything dated today (UTC) or later may still be trading.
    today = _utcnow().date()
    histories: dict[str, dict[str, float]] = {}
    misses = []
    for symbol, cached in zip(symbols, cache.get_json_many([_cache_key(s, today) for s in symbols])):
        closes = _from_cache(symbol, cached)
        if closes is None:
            misses.append(symbol)
        elif closes:
            histories[symbol] = closes
    if not misses:
        return histories

    try:
        batch = yf_client.get_close_history_batch(misses)
    except Exception:
        logger.warning("Failed to fetch price history for %s", misses, exc_info=True)
        return histories

    failed = set(batch.failed)
    for symbol in misses:
        if symbol in failed:
            continue  # transient: retried on the next request
        series = batch.closes.get(symbol)
        closes = {}
        if series is not None:
            closes = {
                ts.date().isoformat(): float(v) for ts, v in series.items() if ts.date() < today and _is_positive(v)
            }
        if not closes:
            logger.info("No price history for %s", symbol)
            cache.set_json(_cache_key(symbol, today), {"closes": {}}, NO_HISTORY_TTL_SECONDS)
            continue
        histories[symbol] = closes
        cache.set_json(_cache_key(symbol, today), {"closes": closes}, PRICE_HISTORY_TTL_SECONDS)
    return histories


def _cache_key(symbol: str, today: date) -> str:
    return f"price_history:{symbol}:5y:{today.isoformat()}"


def _from_cache(symbol: str, cached: dict | None) -> dict[str, float] | None:
    """Cached closes, {} for a symbol known to have none, or None on a miss / invalid entry."""
    if cached is None:
        return None
    closes = cached.get("closes") if isinstance(cached, dict) else None
    try:
        if not isinstance(closes, dict):
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
