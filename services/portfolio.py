"""Portfolio valuation: holdings + daily quotes + FX, all reported in EUR."""

import logging
import math
from collections.abc import Callable

from connectors import cache
from connectors.portfolio import HoldingDto, PortfolioConnector
from connectors.yfinance_client import YFinanceClient
from services.price_change import get_price_changes

logger = logging.getLogger(__name__)

BASE_CURRENCY = "EUR"
# Bounds GET cost: valuation fetches one quote per holding (mirrors the /api/quotes 50-ticker cap).
MAX_HOLDINGS_PER_USER = 50
FX_CACHE_TTL_SECONDS = 6 * 3600
# Yahoo quotes London listings in pence ("GBp"); normalise to pounds.
MINOR_UNIT_CURRENCIES = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}


class UnknownTickerError(Exception):
    pass


class HoldingLimitError(Exception):
    pass


def get_fx_rate(currency: str, yf_client: YFinanceClient) -> float | None:
    """Units of BASE_CURRENCY per 1 unit of `currency`, from the latest daily close."""
    if currency == BASE_CURRENCY:
        return 1.0
    cache_key = f"fx:{currency}{BASE_CURRENCY}"
    cached = cache.get_json(cache_key)
    if cached is not None and _is_finite(cached.get("rate")):
        return cached["rate"]
    try:
        history, _ = yf_client.get_daily_history(f"{currency}{BASE_CURRENCY}=X")
    except Exception:
        logger.warning("Failed to fetch FX rate for %s", currency, exc_info=True)
        return None
    closes = [float(c) for c in history["Close"] if _is_finite(c)] if not history.empty else []
    if not closes or closes[-1] <= 0:
        return None
    rate = closes[-1]
    cache.set_json(cache_key, {"rate": rate}, FX_CACHE_TTL_SECONDS)
    return rate


def resolve_quote(ticker: str, yf_client: YFinanceClient) -> dict:
    """Latest quote for a ticker, raising UnknownTickerError when Yahoo has no usable price."""
    quote = get_price_changes([ticker], yf_client).get(ticker)
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
        if len(existing) >= MAX_HOLDINGS_PER_USER:
            raise HoldingLimitError(ticker)
        # Only new tickers are validated, so editing a held position still works while Yahoo is down.
        resolve_quote(ticker, yf_client)
    return portfolio.upsert_holding(user_id=user_id, ticker=ticker, name=name, shares=shares, avg_cost=avg_cost)


def remove_holding(*, user_id: str, ticker: str, portfolio: PortfolioConnector) -> bool:
    return portfolio.delete_holding(user_id=user_id, ticker=ticker)


def get_portfolio(user_id: str, portfolio: PortfolioConnector, yf_client: YFinanceClient) -> dict:
    holdings = portfolio.list_holdings(user_id)
    quotes = get_price_changes([h.ticker for h in holdings], yf_client) if holdings else {}

    fx_rates: dict[str, float | None] = {}

    def fx_for(currency: str) -> float | None:
        if currency not in fx_rates:
            fx_rates[currency] = get_fx_rate(currency, yf_client)
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
    trading_dates = [r["trading_date"] for r in priced if r["trading_date"]]
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
            "as_of": max(trading_dates) if trading_dates else None,
        },
        "holdings": rows,
    }


def _value_holding(h: HoldingDto, quote: dict | None, fx_for: Callable[[str], float | None]) -> dict:
    row = {
        "ticker": h.ticker,
        "name": h.name,
        "shares": h.shares,
        "avg_cost": h.avg_cost,
        "currency": None,
        "price": None,
        "day_change_percent": None,
        "trading_date": None,
        "fx_rate": None,
        "value": None,
        "cost_basis": None,
        "day_change": None,
        "total_return": None,
        "total_return_percent": None,
    }
    if quote is None:
        return row

    row.update(day_change_percent=quote["change_percent"], trading_date=quote["trading_date"])
    currency = quote.get("currency")
    if not currency:
        # Unknown quote currency: guessing one would silently misvalue the holding.
        return row

    # avg_cost is stored in the quote's unit (e.g. pence for GBp), so it is scaled together with price.
    price, prev_close, avg_cost = quote["close"], quote["prev_close"], h.avg_cost
    if currency in MINOR_UNIT_CURRENCIES:
        currency, divisor = MINOR_UNIT_CURRENCIES[currency]
        price, prev_close, avg_cost = price / divisor, prev_close / divisor, avg_cost / divisor
    row.update(currency=currency, price=price, avg_cost=avg_cost)

    fx = fx_for(currency)
    if fx is None:
        return row
    # Cost basis converted at today's FX: return reflects price move + currency move since purchase is not tracked.
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


def _is_finite(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)
