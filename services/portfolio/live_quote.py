"""Live regular-session quotes per ticker: Redis cache entry format. PortfolioService does the I/O."""

import logging
import math
from datetime import date, datetime

from connectors.yfinance_client import LiveQuoteDto

logger = logging.getLogger(__name__)

LIVE_QUOTE_TTL_SECONDS = 300
# Caps concurrent Yahoo requests per call; a portfolio holds at most 50 tickers.
MAX_WORKERS = 8


def cache_key(ticker: str) -> str:
    return f"live_quote:{ticker}"


def from_cache(ticker: str, cached: dict | None) -> LiveQuoteDto | None:
    """The cached quote, or None on a miss / invalid entry."""
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


def to_json(quote: LiveQuoteDto) -> dict:
    return {
        "price": quote.price,
        "prev_close": quote.prev_close,
        "currency": quote.currency,
        "market_time": quote.market_time.isoformat(),
        "trading_date": quote.trading_date.isoformat(),
    }


def _is_positive(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
