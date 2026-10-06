"""Per-holding classification (sector, country, asset type) for portfolio allocation.

Sources, in order: Redis cache, stored company fundamentals, Yahoo `Ticker.info`. PortfolioService
reads them; this module turns each into a HoldingMetadata.
"""

import logging
from typing import TypedDict

from connectors.company import CompanyClassificationDto

logger = logging.getLogger(__name__)

# Classification rarely changes; a week bounds staleness while keeping Yahoo calls rare.
METADATA_TTL_SECONDS = 7 * 24 * 3600
# Yahoo failures are answered "Other" and retried after this, so a flaky ticker isn't refetched on every request.
FAILED_TTL_SECONDS = 15 * 60
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


def cache_key(ticker: str) -> str:
    return f"holding_meta:{ticker}"


def from_cache(cached: dict | None) -> HoldingMetadata | None:
    """The cached metadata, or None on a miss / partial entry."""
    if cached is None or not set(cached) >= set(HoldingMetadata.__annotations__):
        return None
    return HoldingMetadata(sector=cached["sector"], country=cached["country"], asset_type=cached["asset_type"])


def from_stored(row: CompanyClassificationDto | None) -> HoldingMetadata | None:
    """Metadata from a stored fundamentals row; None when it lacks a sector or country."""
    if not (row and row.sector and row.country):
        return None
    # Only companies (not funds) get fundamentals rows.
    return _metadata(row.sector, row.country, "Stock")


def from_info(ticker: str, info: dict) -> HoldingMetadata | None:
    """Metadata from Yahoo `Ticker.info`; None when Yahoo answered without a quote type."""
    quote_type = str(info.get("quoteType") or "").upper()
    if not quote_type:
        # Yahoo sometimes answers {} instead of raising when flaky; don't cache that for a week.
        logger.info("No metadata for %s", ticker)
        return None
    asset_type = QUOTE_TYPES.get(quote_type, "")
    # Funds span sectors; Yahoo's occasional ETF "sector" is its largest holding's, not the fund's.
    sector = ETF_SECTOR if asset_type == "ETF" else info.get("sector") or ""
    return _metadata(sector, info.get("country") or "", asset_type)


def unknown() -> HoldingMetadata:
    return _metadata("", "", "")


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
