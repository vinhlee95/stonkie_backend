"""Portfolio valuation: holdings + quotes + FX, all reported in EUR. PortfolioService fetches the inputs.

Holdings without a live quote fall back to the last completed daily close and are flagged delayed.
"""

from connectors.portfolio import HoldingDto, LotDto
from connectors.yfinance_client import LiveQuoteDto

BASE_CURRENCY = "EUR"
# Bounds GET cost: valuation fetches one quote per holding (mirrors the /api/quotes 50-ticker cap).
MAX_HOLDINGS_PER_USER = 50
# Bounds a position's lot list (and the GET payload).
MAX_LOTS_PER_HOLDING = 100
# Yahoo quotes London listings in pence ("GBp"); normalise to pounds.
MINOR_UNIT_CURRENCIES = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}


def lot_to_dict(lot: LotDto) -> dict:
    return {
        "id": str(lot.id),
        "shares": lot.shares,
        "price": lot.price,
        "purchased_on": lot.purchased_on.isoformat() if lot.purchased_on else None,
    }


def fx_currencies(holdings: list[HoldingDto], quotes: dict[str, dict]) -> set[str]:
    """Major currencies the holdings are quoted in, i.e. the FX rates value_portfolio needs."""
    currencies = set()
    for h in holdings:
        currency = (quotes.get(h.ticker) or {}).get("currency")
        if currency:
            currencies.add(MINOR_UNIT_CURRENCIES.get(currency, (currency, 1))[0])
    return currencies


def value_portfolio(
    holdings: list[HoldingDto],
    quotes: dict[str, dict],
    metadata: dict[str, dict],
    fx_rates: dict[str, float | None],
) -> dict:
    """`fx_rates` maps each of fx_currencies() to its EUR rate (None when unavailable)."""
    rows = [{**_value_holding(h, quotes.get(h.ticker), fx_rates), **metadata[h.ticker]} for h in holdings]
    priced = [r for r in rows if r["value"] is not None]

    total_value = sum(r["value"] for r in priced)
    total_cost = sum(r["cost_basis"] for r in priced)
    day_change = sum(r["day_change"] for r in priced)
    for r in rows:
        r["weight"] = r["value"] / total_value * 100 if r["value"] is not None and total_value else None
    rows.sort(key=lambda r: r["value"] if r["value"] is not None else -1, reverse=True)

    prev_value = total_value - day_change
    # All rows, not just priced ones: a live quote counts even when its FX rate is missing.
    live_times = [r["as_of"] for r in rows if r["as_of"]]
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
            "delayed_count": sum(1 for r in rows if r["delayed"]),
        },
        "holdings": rows,
    }


def delayed_quote(quote: dict) -> dict:
    """A last-completed-daily-close quote, used when a ticker has no live quote."""
    return {**quote, "as_of": None, "delayed": True}


def live_to_quote(q: LiveQuoteDto) -> dict:
    return {
        "close": q.price,
        "prev_close": q.prev_close,
        "change_percent": round((q.price - q.prev_close) / q.prev_close * 100, 2),
        "currency": q.currency,
        "trading_date": q.trading_date.isoformat(),
        "as_of": q.market_time.isoformat(),
        "delayed": False,
    }


def _value_holding(h: HoldingDto, quote: dict | None, fx_rates: dict[str, float | None]) -> dict:
    row = {
        "ticker": h.ticker,
        "name": h.name,
        "shares": h.shares,
        "avg_cost": h.avg_cost,
        # Newest purchase first; shares/avg_cost above aggregate them.
        "lots": [lot_to_dict(lot) for lot in h.lots],
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

    fx = fx_rates.get(currency)
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
