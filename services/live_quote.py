"""Live regular-session quotes per ticker, cached briefly in Redis."""

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

from connectors import cache
from connectors.yfinance_client import LiveQuoteDto, YFinanceClient

logger = logging.getLogger(__name__)

LIVE_QUOTE_TTL_SECONDS = 300
# Caps concurrent Yahoo requests per call; a portfolio holds at most 50 tickers.
MAX_WORKERS = 8


def get_live_quotes(tickers: list[str], yf_client: YFinanceClient) -> dict[str, LiveQuoteDto]:
    """Live quote per ticker. Tickers whose quote is unavailable or fails are omitted."""
    quotes: dict[str, LiveQuoteDto] = {}
    misses = []
    for ticker in tickers:
        cached = _from_cache(ticker)
        if cached is not None:
            quotes[ticker] = cached
        else:
            misses.append(ticker)
    if not misses:
        return quotes

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(misses))) as pool:
        fetched = pool.map(lambda t: _fetch(t, yf_client), misses)
    for ticker, quote in zip(misses, fetched):
        if quote is not None:
            quotes[ticker] = quote
            cache.set_json(_cache_key(ticker), _to_json(quote), LIVE_QUOTE_TTL_SECONDS)
    return quotes


def _fetch(ticker: str, yf_client: YFinanceClient) -> LiveQuoteDto | None:
    try:
        quote = yf_client.get_live_quote(ticker)
    except Exception:
        logger.warning("Failed to fetch live quote for %s", ticker, exc_info=True)
        return None
    if quote is None:
        logger.info("No live quote for %s", ticker)
    return quote


def _cache_key(ticker: str) -> str:
    return f"live_quote:{ticker}"


def _from_cache(ticker: str) -> LiveQuoteDto | None:
    cached = cache.get_json(_cache_key(ticker))
    if cached is None:
        return None
    try:
        if not (_is_positive(cached["price"]) and _is_positive(cached["prev_close"])):
            raise ValueError("non-positive or non-numeric price")
        return LiveQuoteDto(
            price=cached["price"],
            prev_close=cached["prev_close"],
            currency=cached["currency"],
            market_time=datetime.fromisoformat(cached["market_time"]),
            trading_date=date.fromisoformat(cached["trading_date"]),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("Invalid cached live quote for %s", ticker)
        return None


def _is_positive(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _to_json(quote: LiveQuoteDto) -> dict:
    return {
        "price": quote.price,
        "prev_close": quote.prev_close,
        "currency": quote.currency,
        "market_time": quote.market_time.isoformat(),
        "trading_date": quote.trading_date.isoformat(),
    }
