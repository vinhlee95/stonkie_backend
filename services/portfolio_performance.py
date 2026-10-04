"""Portfolio vs S&P 500 value history in EUR, for the dashboard performance chart.

Lot purchase dates are not used yet, so the series back-tests the *current* holdings: today's
shares priced at each day's close and FX rate. Both lines are value levels; clients rebase them per range.
"""

from dataclasses import dataclass

import pandas as pd

from connectors.portfolio import PortfolioConnector
from connectors.yfinance_client import YFinanceClient
from services.portfolio import BASE_CURRENCY, MINOR_UNIT_CURRENCIES, get_quotes
from services.price_history import get_close_histories

BENCHMARK_SYMBOL = "^GSPC"
BENCHMARK_CURRENCY = "USD"


@dataclass(frozen=True)
class _Position:
    ticker: str
    shares: float
    currency: str
    divisor: int


@dataclass(frozen=True)
class EurSeries:
    """Daily EUR series for the priced holdings and the benchmark, aligned on one session index."""

    index: pd.DatetimeIndex
    # EUR value of one share on each date.
    prices: dict[str, pd.Series]
    shares: dict[str, float]
    benchmark: pd.Series
    # First real close per ticker; earlier dates in `prices` are back-filled, not traded.
    first_close: dict[str, pd.Timestamp]

    def portfolio_value(self) -> pd.Series:
        return sum(self.prices[t] * self.shares[t] for t in self.prices)


def get_performance(user_id: str, portfolio: PortfolioConnector, yf_client: YFinanceClient) -> dict:
    holdings = portfolio.list_holdings(user_id)
    result = {"base_currency": BASE_CURRENCY, "points": [], "excluded": []}
    if not holdings:
        return result

    series, excluded = load_eur_series(holdings, yf_client)
    result["excluded"] = excluded
    if series is None:
        return result

    portfolio_value = series.portfolio_value()
    result["points"] = [
        {"date": day.date().isoformat(), "portfolio_value": round(pv, 2), "benchmark_value": round(bv, 2)}
        for day, pv, bv in zip(series.index, portfolio_value, series.benchmark)
    ]
    return result


def load_eur_series(holdings: list, yf_client: YFinanceClient) -> tuple[EurSeries | None, list[str]]:
    """EUR series for `holdings` (anything with .ticker and .shares) plus the sorted tickers left out
    for lacking a quote currency or history. None when no holding or the benchmark can be priced."""
    if not holdings:
        return None, []
    quotes = get_quotes([h.ticker for h in holdings], yf_client)
    positions, excluded = [], []
    for h in holdings:
        currency = (quotes.get(h.ticker) or {}).get("currency")
        if not currency:
            # Unknown quote currency: guessing one would silently misvalue the holding.
            excluded.append(h.ticker)
            continue
        currency, divisor = MINOR_UNIT_CURRENCIES.get(currency, (currency, 1))
        positions.append(_Position(h.ticker, h.shares, currency, divisor))

    currencies = {p.currency for p in positions} | {BENCHMARK_CURRENCY}
    fx_symbols = {c: _fx_symbol(c) for c in currencies if c != BASE_CURRENCY}
    histories = get_close_histories(
        [p.ticker for p in positions] + [BENCHMARK_SYMBOL] + list(fx_symbols.values()), yf_client
    )

    def has_history(currency: str, symbol: str) -> bool:
        return symbol in histories and (currency == BASE_CURRENCY or fx_symbols[currency] in histories)

    priced = [p for p in positions if has_history(p.currency, p.ticker)]
    excluded = sorted(excluded + [p.ticker for p in positions if p not in priced])
    if not priced or not has_history(BENCHMARK_CURRENCY, BENCHMARK_SYMBOL):
        return None, excluded

    # Session dates of any holding or the benchmark; FX has its own calendar, so it is only sampled.
    series = {symbol: _to_series(closes) for symbol, closes in histories.items()}
    index = series[BENCHMARK_SYMBOL].index
    for p in priced:
        index = index.union(series[p.ticker].index)

    def aligned(symbol: str) -> pd.Series:
        return _align(series[symbol], index)

    def fx(currency: str) -> pd.Series | float:
        return 1.0 if currency == BASE_CURRENCY else aligned(fx_symbols[currency])

    return (
        EurSeries(
            index=index,
            prices={p.ticker: aligned(p.ticker) / p.divisor * fx(p.currency) for p in priced},
            shares={p.ticker: p.shares for p in priced},
            benchmark=aligned(BENCHMARK_SYMBOL) * fx(BENCHMARK_CURRENCY),
            first_close={p.ticker: series[p.ticker].index[0] for p in priced},
        ),
        excluded,
    )


def period_returns(series: EurSeries) -> dict:
    """1W / 1M / YTD return (%) of the back-tested portfolio and the S&P 500 to the last close.
    A period is left out when the series doesn't reach back to its start."""
    last = series.index[-1]
    value = series.portfolio_value()
    starts = {
        "1W": last - pd.Timedelta(days=7),
        "1M": last - pd.DateOffset(months=1),
        "YTD": pd.Timestamp(year=last.year - 1, month=12, day=31),
    }
    periods = {}
    for label, start in starts.items():
        base_dates = series.index[series.index <= start]
        if base_dates.empty:
            continue
        base = base_dates[-1]
        periods[label] = {
            "portfolio": round(float(value[last] / value[base] - 1) * 100, 2),
            "benchmark": round(float(series.benchmark[last] / series.benchmark[base] - 1) * 100, 2),
        }
    return {"as_of": last.date().isoformat(), "periods": periods}


def _fx_symbol(currency: str) -> str:
    return f"{currency}{BASE_CURRENCY}=X"


def _to_series(closes: dict[str, float]) -> pd.Series:
    series = pd.Series(closes, dtype=float)
    series.index = pd.to_datetime(series.index)
    return series.sort_index()


def _align(series: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Value on each date of `index`: the last close on or before it (holidays, other exchanges'
    calendars), or the first close for dates before the symbol has any (late listings)."""
    return series.reindex(series.index.union(index)).ffill().bfill().reindex(index)
