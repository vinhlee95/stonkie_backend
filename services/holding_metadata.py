"""Per-holding classification (sector, country, asset type) for portfolio allocation.

Sources, in order: Redis cache, stored company fundamentals, Yahoo `Ticker.info`.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict

from connectors import cache
from connectors.company import CompanyConnector
from connectors.yfinance_client import YFinanceClient

logger = logging.getLogger(__name__)

# Classification rarely changes; a week bounds staleness while keeping Yahoo calls rare.
METADATA_TTL_SECONDS = 7 * 24 * 3600
MAX_WORKERS = 8
UNKNOWN = "Other"
ETF_SECTOR = "Diversified"
QUOTE_TYPES = {"EQUITY": "Stock", "ETF": "ETF"}
# Alpha Vantage spells some countries differently from Yahoo.
COUNTRY_ALIASES = {"USA": "United States"}


class HoldingMetadata(TypedDict):
    sector: str
    country: str
    asset_type: str


def get_holdings_metadata(
    tickers: list[str], yf_client: YFinanceClient, companies: CompanyConnector | None = None
) -> dict[str, HoldingMetadata]:
    """Metadata for every ticker; unknown fields are "Other". Never raises."""
    result: dict[str, HoldingMetadata] = {}
    misses = []
    for ticker in tickers:
        cached = cache.get_json(_cache_key(ticker))
        if cached is not None and set(cached) >= set(HoldingMetadata.__annotations__):
            result[ticker] = HoldingMetadata(
                sector=cached["sector"], country=cached["country"], asset_type=cached["asset_type"]
            )
        else:
            misses.append(ticker)
    if not misses:
        return result

    stored = _stored_classifications(misses, companies or CompanyConnector())
    fetch = []
    for ticker in misses:
        sector, country = stored.get(ticker, ("", ""))
        if sector:
            # Only companies (not funds) get fundamentals rows.
            _store(result, ticker, _metadata(sector, country, "Stock"))
        else:
            fetch.append(ticker)

    if fetch:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(fetch))) as pool:
            fetched = pool.map(lambda t: _from_yahoo(t, yf_client), fetch)
        for ticker, meta in zip(fetch, fetched):
            if meta is None:
                # Transient failure: answer "Other" now but retry on the next request.
                result[ticker] = _metadata("", "", "")
            else:
                _store(result, ticker, meta)
    return result


def _stored_classifications(tickers: list[str], companies: CompanyConnector) -> dict[str, tuple[str, str]]:
    try:
        return companies.get_classifications(tickers)
    except Exception:
        logger.warning("Failed to read stored classifications", exc_info=True)
        return {}


def _from_yahoo(ticker: str, yf_client: YFinanceClient) -> HoldingMetadata | None:
    try:
        info = yf_client.get_info(ticker)
    except Exception:
        logger.warning("Failed to fetch metadata for %s", ticker, exc_info=True)
        return None
    asset_type = QUOTE_TYPES.get(str(info.get("quoteType") or "").upper(), "")
    sector = info.get("sector") or (ETF_SECTOR if asset_type == "ETF" else "")
    return _metadata(sector, info.get("country") or "", asset_type)


def _metadata(sector: str, country: str, asset_type: str) -> HoldingMetadata:
    return HoldingMetadata(
        sector=_normalise(sector) or UNKNOWN,
        country=COUNTRY_ALIASES.get(country, country) or UNKNOWN,
        asset_type=asset_type or UNKNOWN,
    )


def _normalise(label: str) -> str:
    """Alpha Vantage upper-cases sectors ("CONSUMER CYCLICAL"); Yahoo title-cases them."""
    label = label.strip()
    return label.title() if label.isupper() else label


def _store(result: dict[str, HoldingMetadata], ticker: str, meta: HoldingMetadata) -> None:
    result[ticker] = meta
    cache.set_json(_cache_key(ticker), dict(meta), METADATA_TTL_SECONDS)


def _cache_key(ticker: str) -> str:
    return f"holding_meta:{ticker}"
