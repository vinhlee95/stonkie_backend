"""Portfolio valuation: holdings + live quotes + FX, all reported in EUR.

Holdings without a live quote fall back to the last completed daily close and are flagged delayed.
"""

import logging
from collections.abc import Callable

from connectors.fx import FxConnector
from connectors.portfolio import HoldingDto, HoldingLimitExceeded, PortfolioConnector
from connectors.yfinance_client import LiveQuoteDto, YFinanceClient
from services.live_quote import get_live_quotes
from services.price_change import PriceFetchError, get_price_change, get_price_changes

logger = logging.getLogger(__name__)

BASE_CURRENCY = "EUR"
# Bounds GET cost: valuation fetches one quote per holding (mirrors the /api/quotes 50-ticker cap).
MAX_HOLDINGS_PER_USER = 50
# Yahoo quotes London listings in pence ("GBp"); normalise to pounds.
MINOR_UNIT_CURRENCIES = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}


class UnknownTickerError(Exception):
    pass


class HoldingLimitError(Exception):
    pass


class QuoteUnavailableError(Exception):
    """Yahoo could not be reached, so the ticker could not be validated; retryable."""


def resolve_quote(ticker: str, yf_client: YFinanceClient) -> dict:
    """Latest quote for a ticker. Raises UnknownTickerError when Yahoo has no usable price and
    QuoteUnavailableError when the fetch itself fails."""
    try:
        quote = get_price_change(ticker, yf_client)
    except PriceFetchError:
        raise QuoteUnavailableError(ticker) from None
    if quote is None:
        raise UnknownTickerError(ticker)
    return quote


def save_holding(
    *,
    user_id: str,
    ticker: str,
    name: str | None,
    shares: float,
    avg_cost: float,
    portfolio: PortfolioConnector,
    yf_client: YFinanceClient,
) -> HoldingDto:
    existing = {h.ticker for h in portfolio.list_holdings(user_id)}
    if ticker not in existing:
        # Fail fast before hitting Yahoo; the connector re-checks atomically on insert.
        if len(existing) >= MAX_HOLDINGS_PER_USER:
            raise HoldingLimitError(ticker)
        # Only new tickers are validated, so editing a held position still works while Yahoo is down.
        resolve_quote(ticker, yf_client)
    try:
        return portfolio.upsert_holding(
            user_id=user_id,
            ticker=ticker,
            name=name,
            shares=shares,
            avg_cost=avg_cost,
            max_holdings=MAX_HOLDINGS_PER_USER,
        )
    except HoldingLimitExceeded:
        raise HoldingLimitError(ticker) from None


def remove_holding(*, user_id: str, ticker: str, portfolio: PortfolioConnector) -> bool:
    return portfolio.delete_holding(user_id=user_id, ticker=ticker)


def get_portfolio(
    user_id: str, portfolio: PortfolioConnector, yf_client: YFinanceClient, fx: FxConnector | None = None
) -> dict:
    fx = fx or FxConnector(yf_client)
    holdings = portfolio.list_holdings(user_id)
    quotes = _quotes([h.ticker for h in holdings], yf_client)

    fx_rates: dict[str, float | None] = {}

    def fx_for(currency: str) -> float | None:
        if currency not in fx_rates:
            fx_rates[currency] = fx.get_live_rate(currency, BASE_CURRENCY)
        return fx_rates[currency]

    rows = [_value_holding(h, quotes.get(h.ticker), fx_for) for h in holdings]
    priced = [r for r in rows if r["value"] is not None]

    total_value = sum(r["value"] for r in priced)
    total_cost = sum(r["cost_basis"] for r in priced)
    day_change = sum(r["day_change"] for r in priced)
    for r in rows:
        r["weight"] = r["value"] / total_value * 100 if r["value"] is not None and total_value else None
    rows.sort(key=lambda r: r["value"] if r["value"] is not None else -1, reverse=True)

    prev_value = total_value - day_change
    live_times = [r["as_of"] for r in priced if r["as_of"]]
    return {
        "base_currency": BASE_CURRENCY,
        "summary": {
            "holdings_count": len(rows),
            "priced_count": len(priced),
            "total_value": total_value,
            "total_cost": total_cost,
            "total_return": total_value - total_cost,
            "total_return_percent": (total_value / total_cost - 1) * 100 if total_cost else 0.0,
            "day_change": day_change,
            "day_change_percent": day_change / prev_value * 100 if prev_value else 0.0,
            # ISO UTC timestamps sort chronologically as strings.
            "as_of": max(live_times) if live_times else None,
            "delayed_count": sum(1 for r in priced if r["delayed"]),
        },
        "holdings": rows,
    }


def _quotes(tickers: list[str], yf_client: YFinanceClient) -> dict[str, dict]:
    """Live quote per ticker; tickers without one fall back to the last completed daily close."""
    if not tickers:
        return {}
    quotes = {t: _live_to_quote(q) for t, q in get_live_quotes(tickers, yf_client).items()}
    missing = [t for t in tickers if t not in quotes]
    if missing:
        for ticker, quote in get_price_changes(missing, yf_client).items():
            quotes[ticker] = {**quote, "as_of": None, "delayed": True}
    return quotes


def _live_to_quote(q: LiveQuoteDto) -> dict:
    return {
        "close": q.price,
        "prev_close": q.prev_close,
        "change_percent": round((q.price - q.prev_close) / q.prev_close * 100, 2),
        "currency": q.currency,
        "trading_date": q.trading_date.isoformat(),
        "as_of": q.market_time.isoformat(),
        "delayed": False,
    }


def _value_holding(h: HoldingDto, quote: dict | None, fx_for: Callable[[str], float | None]) -> dict:
    row = {
        "ticker": h.ticker,
        "name": h.name,
        "shares": h.shares,
        "avg_cost": h.avg_cost,
        "currency": None,
        # Raw Yahoo currency (e.g. "GBp"): the unit avg_cost is entered and returned in.
        "quote_currency": None,
        "price": None,
        "day_change_percent": None,
        "trading_date": None,
        # Live quote time (ISO UTC); None for delayed rows priced at the last daily close.
        "as_of": None,
        "delayed": False,
        "fx_rate": None,
        "value": None,
        "cost_basis": None,
        "day_change": None,
        "total_return": None,
        "total_return_percent": None,
    }
    if quote is None:
        return row

    row.update(
        day_change_percent=quote["change_percent"],
        trading_date=quote["trading_date"],
        as_of=quote["as_of"],
        delayed=quote["delayed"],
    )
    currency = quote.get("currency")
    if not currency:
        # Unknown quote currency: guessing one would silently misvalue the holding.
        return row

    # avg_cost is stored in the quote's unit (e.g. pence for GBp), so it is scaled together with price.
    price, prev_close, avg_cost = quote["close"], quote["prev_close"], h.avg_cost
    if currency in MINOR_UNIT_CURRENCIES:
        currency, divisor = MINOR_UNIT_CURRENCIES[currency]
        price, prev_close, avg_cost = price / divisor, prev_close / divisor, avg_cost / divisor
    # Row avg_cost stays as stored (in quote_currency) so clients can round-trip it via PUT.
    row.update(currency=currency, quote_currency=quote["currency"], price=price)

    fx = fx_for(currency)
    if fx is None:
        return row
    # Cost basis converted at the current FX: return reflects price move + currency move since purchase is not tracked.
    value = h.shares * price * fx
    cost_basis = h.shares * avg_cost * fx
    row.update(
        fx_rate=fx,
        value=value,
        cost_basis=cost_basis,
        day_change=h.shares * (price - prev_close) * fx,
        total_return=value - cost_basis,
        total_return_percent=(value / cost_basis - 1) * 100 if cost_basis else 0.0,
    )
    return row
