"""Yahoo symbol search for holdings: stocks, ETFs and funds with exchange suffixes (SXR8 → SXR8.DE)."""

from connectors import cache
from connectors.yfinance_client import YFinanceClient

CACHE_TTL_SECONDS = 3600
# After a Yahoo failure, fail fast for a while so a typeahead can't pin threadpool workers on timeouts.
OUTAGE_KEY = "ticker_search:__yahoo_down"
OUTAGE_TTL_SECONDS = 60
# Holdable instruments the portfolio can price; skips indices, currencies, futures, options.
QUOTE_TYPES = {"EQUITY", "ETF", "MUTUALFUND"}


class TickerSearchError(Exception):
    """Yahoo search failed (as opposed to returning no matches)."""


def search_tickers(query: str, yf_client: YFinanceClient) -> list[dict]:
    """Matches as [{symbol, name, exchange}]; raises TickerSearchError when Yahoo fails."""
    normalised = query.strip().lower()
    cache_key = f"ticker_search:{normalised}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached["results"]
    if cache.get_json(OUTAGE_KEY) is not None:
        raise TickerSearchError(normalised)
    try:
        quotes = yf_client.search(normalised)
    except Exception as exc:
        cache.set_json(OUTAGE_KEY, {"down": True}, OUTAGE_TTL_SECONDS)
        raise TickerSearchError(normalised) from exc
    results = [_to_result(q) for q in quotes if _is_holdable(q)]
    cache.set_json(cache_key, {"results": results}, CACHE_TTL_SECONDS)
    return results


def _is_holdable(quote: dict) -> bool:
    return bool(quote.get("symbol")) and bool(quote.get("isYahooFinance")) and quote.get("quoteType") in QUOTE_TYPES


def _to_result(quote: dict) -> dict:
    symbol = quote["symbol"].upper()
    return {
        "symbol": symbol,
        "name": quote.get("longname") or quote.get("shortname") or symbol,
        "exchange": quote.get("exchDisp"),
    }
