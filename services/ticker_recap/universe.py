"""Which tickers the daily ticker recap job covers: popular tickers, every held ticker, then env overrides.

Held tickers are kept only on markets the recap pipeline supports (search routing + trading-day tz)
and when Yahoo lists them as equities (funds, crypto, indices have near-empty ticker news). The job fetches
holdings; the Yahoo lookup is injected.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from connectors.portfolio import HeldTickerDto as HeldTicker

logger = logging.getLogger(__name__)

DEFAULT_MAX_TICKERS = 100
# Exchange suffix → recap market. No suffix is US (dashed share classes like BRK-B included).
SUFFIX_MARKETS = {"HE": "FI", "VN": "VN"}
# Only operating companies have ticker news worth a recap; ETFs, funds, crypto, indices and FX are skipped.
RECAP_QUOTE_TYPES = {"EQUITY"}

__all__ = ["DEFAULT_MAX_TICKERS", "HeldTicker", "TickerInfo", "build_universe", "market_for_ticker", "yahoo_lookup"]


@dataclass(frozen=True)
class TickerInfo:
    """Yahoo's view of a symbol: the company name used in prompts and search, and its quoteType."""

    name: str | None
    quote_type: str | None


class InfoClient(Protocol):
    def get_info(self, ticker: str) -> dict: ...


def market_for_ticker(ticker: str) -> str | None:
    """Recap market for a Yahoo symbol, or None when its exchange isn't supported."""
    if "." not in ticker:
        return "US"
    return SUFFIX_MARKETS.get(ticker.rsplit(".", 1)[1].upper())


def build_universe(
    popular: dict[str, dict[str, str]],
    held: list[HeldTicker],
    overrides: dict[str, dict[str, str]],
    *,
    lookup: Callable[[str], TickerInfo | None],
    cap: int,
) -> tuple[dict[str, dict[str, str]], dict[str, int]]:
    """`{ticker: {name, market}}` in run order (popular, overrides, then held as given) plus filter stats.

    The fixed set runs first so a run cut short by its timeout still covers it. Overrides replace entries and
    are never filtered. `cap` bounds the whole catalog but only cuts held tickers (most-held first, so the
    least-held go); held tickers past the cap aren't looked up. Names come from `lookup`, never from the
    holdings (free text any user can set). A failed lookup still tries the ticker, under its own name."""
    fixed = popular.keys() | overrides.keys()
    room = max(0, cap - len(fixed))
    stats = {"popular": len(popular), "held": 0, "dropped_market": 0, "dropped_type": 0, "capped": 0}
    kept: dict[str, dict[str, str]] = {}
    for item in held:
        if item.ticker in fixed or item.ticker in kept:
            continue
        if len(kept) >= room:
            stats["capped"] += 1
            continue
        market = market_for_ticker(item.ticker)
        if market is None:
            stats["dropped_market"] += 1
            continue
        info = lookup(item.ticker)
        if info is not None and info.quote_type not in RECAP_QUOTE_TYPES:
            stats["dropped_type"] += 1
            continue
        kept[item.ticker] = {"name": (info.name if info else None) or item.ticker, "market": market}
    stats["held"] = len(kept)
    return {**popular, **overrides, **kept}, stats


def yahoo_lookup(yf_client: InfoClient) -> Callable[[str], TickerInfo | None]:
    """Name + quoteType from Yahoo `info`; None when Yahoo fails or knows nothing about the symbol."""

    def lookup(ticker: str) -> TickerInfo | None:
        try:
            info = yf_client.get_info(ticker)
        except Exception:
            logger.warning("ticker_recap: Yahoo info lookup failed for %s", ticker, exc_info=True)
            return None
        if not info.get("quoteType"):
            return None
        return TickerInfo(name=info.get("longName") or info.get("shortName"), quote_type=info["quoteType"])

    return lookup
