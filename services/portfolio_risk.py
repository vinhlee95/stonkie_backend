"""Portfolio risk stats from EUR daily series: beta vs S&P 500, 1y volatility, max drawdown, concentration.

Returns use the series' session index, so a holding shows a 0% day when only another exchange traded.
Metrics backed by fewer than MIN_POINTS real returns are None rather than a noisy guess.
"""

import math
from collections import defaultdict

import pandas as pd

from services.portfolio_performance import EurSeries

RISK_WINDOW = pd.DateOffset(years=1)
MIN_POINTS = 60
TRADING_DAYS = 252


def compute_risk(series: EurSeries | None, rows: list[dict]) -> dict:
    """`rows` are valued holdings as returned by services.portfolio.get_portfolio (weight in %)."""
    holdings: dict[str, dict] = {}
    portfolio = {"beta": None, "vol_1y": None, "max_drawdown_1y": None}
    if series is not None:
        # By date, not count: the index is the union of several exchanges' calendars.
        window = series.index[series.index >= series.index[-1] - RISK_WINDOW]
        for ticker, prices in series.prices.items():
            dates = window[window >= series.first_close[ticker]]
            beta, vol = _beta_and_vol(prices, series.benchmark, dates)
            holdings[ticker] = {"beta": beta, "vol_1y": vol}
        value = series.portfolio_value()
        # Only dates every holding really traded: back-filled flat prices would understate risk.
        if series.first_close:
            window = window[window >= max(series.first_close.values())]
        beta, vol = _beta_and_vol(value, series.benchmark, window)
        if vol is not None:
            in_window = value.reindex(window)
            drawdown = (in_window / in_window.cummax() - 1).min() * 100
            portfolio = {"beta": beta, "vol_1y": vol, "max_drawdown_1y": round(float(drawdown), 2)}
    return {"holdings": holdings, "portfolio": portfolio, "concentration": _concentration(rows)}


def _beta_and_vol(
    prices: pd.Series, benchmark: pd.Series, dates: pd.DatetimeIndex
) -> tuple[float | None, float | None]:
    if len(dates) < MIN_POINTS + 1:
        return None, None
    returns = prices.reindex(dates).pct_change().dropna()
    bench = benchmark.reindex(dates).pct_change().dropna()
    bench_var = bench.var()
    beta = returns.cov(bench) / bench_var if bench_var > 0 else None
    vol = returns.std() * math.sqrt(TRADING_DAYS) * 100
    return (round(float(beta), 2) if beta is not None else None), round(float(vol), 2)


def _concentration(rows: list[dict]) -> dict:
    weighted = [r for r in rows if r.get("weight") is not None]
    if not weighted:
        return {"top3_weight": None, "largest_sector": None, "largest_country": None}
    top3 = sorted((r["weight"] for r in weighted), reverse=True)[:3]
    return {
        "top3_weight": round(sum(top3), 2),
        "largest_sector": _largest(weighted, "sector"),
        "largest_country": _largest(weighted, "country"),
    }


def _largest(rows: list[dict], key: str) -> dict:
    totals: dict[str, float] = defaultdict(float)
    for r in rows:
        totals[r.get(key) or "Unknown"] += r["weight"]
    name, weight = max(totals.items(), key=lambda item: item[1])
    return {"name": name, "weight": round(weight, 2)}
