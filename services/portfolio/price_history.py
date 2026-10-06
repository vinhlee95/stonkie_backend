"""5y of completed-session daily closes per Yahoo symbol: Redis cache entry format and bar filtering.
PortfolioService does the I/O."""

import logging
import math
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)

# Entries are keyed by UTC date, so every symbol in a request shares one "completed sessions" cutoff
# and a holding's newest close never sits next to a benchmark cached before that session closed.
PRICE_HISTORY_TTL_SECONDS = 24 * 3600
# Symbols Yahoo has no prices for are remembered briefly so each dashboard load doesn't re-download
# them. Failed fetches are never cached this way; kept short anyway since a shared ^GSPC / FX key
# marked empty blanks every user's chart.
NO_HISTORY_TTL_SECONDS = 300


def cache_key(symbol: str, today: date) -> str:
    return f"price_history:{symbol}:5y:{today.isoformat()}"


def from_cache(symbol: str, cached: dict | None) -> dict[str, float] | None:
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


def completed_closes(series: pd.Series | None, today: date) -> dict[str, float]:
    """{ISO date: close} for valid bars dated before `today` (UTC). Bars are dated in exchange-local
    time; anything dated today or later may still be trading."""
    if series is None:
        return {}
    return {ts.date().isoformat(): float(v) for ts, v in series.items() if ts.date() < today and _is_positive(v)}


def _is_positive(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
