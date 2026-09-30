"""Yahoo symbol search for holdings: stocks, ETFs and funds with exchange suffixes (SXR8 → SXR8.DE)."""

from connectors import cache
from connectors.yfinance_client import TickerSearchQuoteDto, YahooSearchUnavailableError, YFinanceClient

CACHE_TTL_SECONDS = 3600
# After a Yahoo failure, fail fast for a while so a typeahead can't pin threadpool workers on timeouts.
# Own namespace so no query string can collide with it.
OUTAGE_KEY = "ticker_search_outage:yahoo"
OUTAGE_TTL_SECONDS = 60
# Holdable instruments the portfolio can price; skips indices, currencies, futures, options.
QUOTE_TYPES = {"EQUITY", "ETF", "MUTUALFUND"}


class TickerSearchError(Exception):
    """Yahoo search failed (as opposed to returning no matches)."""


def search_tickers(query: str, yf_client: YFinanceClient) -> list[dict]:
    """Matches as [{symbol, name, exchange}]; raises TickerSearchError when Yahoo fails."""
    normalised = query.strip().lower()
    cache_key = f"ticker_search:{normalised}"
    cached, outage = cache.get_json_many([cache_key, OUTAGE_KEY])
    if cached is not None:
        return cached["results"]
    if outage is not None:
        raise TickerSearchError(normalised)
    try:
        quotes = yf_client.search(normalised)
    except YahooSearchUnavailableError as exc:
        cache.set_json(OUTAGE_KEY, {"down": True}, OUTAGE_TTL_SECONDS)
        raise TickerSearchError(normalised) from exc
    except Exception as exc:
        # Query-specific failure: fail this request only, don't trip the shared breaker.
        raise TickerSearchError(normalised) from exc
    results = [_to_result(q) for q in quotes if _is_holdable(q)]
    # yfinance returns empty quotes for some Yahoo glitches (e.g. a non-JSON 200), so misses aren't cached.
    if results:
        cache.set_json(cache_key, {"results": results}, CACHE_TTL_SECONDS)
    return results


def _is_holdable(quote: TickerSearchQuoteDto) -> bool:
    return quote.is_yahoo_finance and quote.quote_type in QUOTE_TYPES


def _to_result(quote: TickerSearchQuoteDto) -> dict:
    symbol = quote.symbol.upper()
    return {"symbol": symbol, "name": quote.name or symbol, "exchange": quote.exchange}
