"""FX rates from Yahoo daily closes, cached in Redis."""

import logging
import math
from datetime import UTC, datetime

from connectors import cache
from connectors.yfinance_client import YFinanceClient

logger = logging.getLogger(__name__)

FX_CACHE_TTL_SECONDS = 6 * 3600


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FxConnector:
    def __init__(self, yf_client: YFinanceClient | None = None):
        self._yf_client = yf_client or YFinanceClient()

    def get_rate(self, currency: str, base: str) -> float | None:
        """Units of `base` per 1 unit of `currency`, from the latest completed daily close."""
        if currency == base:
            return 1.0
        cache_key = f"fx:{currency}{base}"
        cached = cache.get_json(cache_key)
        if cached is not None and _is_finite(cached.get("rate")):
            return cached["rate"]
        try:
            history, _ = self._yf_client.get_daily_history(f"{currency}{base}=X")
        except Exception:
            logger.warning("Failed to fetch FX rate %s%s", currency, base, exc_info=True)
            return None
        if not history.empty:
            # FX trades around the clock, so today's bar is still forming; use the last completed
            # close to match the completed-session equity prices it is multiplied with.
            last_ts = history.index[-1]
            if last_ts.date() == _utcnow().astimezone(last_ts.tzinfo).date():
                history = history.iloc[:-1]
        closes = [float(c) for c in history["Close"] if _is_finite(c)] if not history.empty else []
        if not closes or closes[-1] <= 0:
            return None
        rate = closes[-1]
        cache.set_json(cache_key, {"rate": rate}, FX_CACHE_TTL_SECONDS)
        return rate


def _is_finite(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)
