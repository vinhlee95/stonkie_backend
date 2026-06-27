"""Daily price change per ticker, computed from completed trading day closes."""

import logging
import math
from datetime import UTC, datetime

from connectors import cache
from connectors.yfinance_client import YFinanceClient

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 6 * 3600

# Conservative session-end fallback: latest regular close among supported exchanges.
# A bar dated today is treated as in-progress before this local hour.
SESSION_END_FALLBACK_HOUR = 18


def _utcnow() -> datetime:
    return datetime.now(UTC)


def get_price_changes(tickers: list[str], yf_client: YFinanceClient) -> dict[str, dict]:
    quotes: dict[str, dict] = {}
    for ticker in tickers:
        cache_key = f"price_change:{ticker}"
        cached = cache.get_json(cache_key)
        if cached is not None and _is_valid_quote(cached):
            quotes[ticker] = cached
            continue
        try:
            quote = _price_change_for_ticker(ticker, yf_client)
        except Exception:
            logger.warning("Failed to compute price change for %s", ticker, exc_info=True)
            continue
        if quote is None:
            logger.info("Insufficient price history for %s, omitting", ticker)
            continue
        quotes[ticker] = quote
        cache.set_json(cache_key, quote, CACHE_TTL_SECONDS)
    return quotes


def _price_change_for_ticker(ticker: str, yf_client: YFinanceClient) -> dict | None:
    history, currency = yf_client.get_daily_history(ticker)
    history = _drop_in_progress_bar(history)
    if len(history) < 2:
        return None
    close = float(history["Close"].iloc[-1])
    prev_close = float(history["Close"].iloc[-2])
    if math.isnan(close):
        # Yahoo intermittently returns the latest daily bar with a NaN Close
        # (Open/High/Low/Volume present). Fall back to the live quote snapshot.
        quote = yf_client.get_quote(ticker)
        if quote is not None:
            close = quote.get("last_price")
            prev_close = quote.get("prev_close")
    if not _is_finite(close) or not _is_finite(prev_close) or prev_close == 0:
        # Never emit non-finite values: they break JSON serialization (and would
        # 500 the whole batch). Omit the ticker instead.
        return None
    return {
        "trading_date": history.index[-1].date().isoformat(),
        "close": round(close, 2),
        "prev_close": round(prev_close, 2),
        "change": round(close - prev_close, 2),
        "change_percent": round((close - prev_close) / prev_close * 100, 2),
        "currency": currency,
    }


def _is_finite(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def _is_valid_quote(quote: dict) -> bool:
    # Guards against non-finite values that 500 JSON serialization — including
    # NaN entries cached before this guard existed.
    return all(_is_finite(quote.get(field)) for field in ("close", "prev_close", "change", "change_percent"))


def _drop_in_progress_bar(history):
    if history.empty:
        return history
    last_ts = history.index[-1]
    local_now = _utcnow().astimezone(last_ts.tzinfo)
    if last_ts.date() == local_now.date() and local_now.hour < SESSION_END_FALLBACK_HOUR:
        return history.iloc[:-1]
    return history
