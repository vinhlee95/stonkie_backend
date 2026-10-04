import math

import pandas as pd
import pytest

from services.portfolio_performance import EurSeries
from services.portfolio_risk import MIN_POINTS, compute_risk


def levels(returns: list[float], start: float = 100.0) -> list[float]:
    out = [start]
    for r in returns:
        out.append(out[-1] * (1 + r))
    return out


def make_series(prices: dict[str, list[float]], benchmark: list[float], first_close: dict[str, int] | None = None):
    index = pd.bdate_range("2025-01-01", periods=len(benchmark))
    first_close = first_close or {}
    return EurSeries(
        index=index,
        prices={t: pd.Series(v, index=index) for t, v in prices.items()},
        shares={t: 1.0 for t in prices},
        benchmark=pd.Series(benchmark, index=index),
        first_close={t: index[first_close.get(t, 0)] for t in prices},
    )


BENCH_RETURNS = [0.01 if i % 2 == 0 else -0.008 for i in range(300)]


def test_beta_and_vol_of_a_leveraged_holding():
    series = make_series(
        {"LEV": levels([2 * r for r in BENCH_RETURNS]), "MKT": levels(BENCH_RETURNS)},
        levels(BENCH_RETURNS),
    )

    risk = compute_risk(series, [])

    assert risk["holdings"]["LEV"]["beta"] == pytest.approx(2.0, abs=0.01)
    assert risk["holdings"]["MKT"]["beta"] == pytest.approx(1.0, abs=0.01)
    # Last year by date: 2025-01-01 + 300 business days → window from the same date a year before the end.
    index = series.index
    window = index[index >= index[-1] - pd.DateOffset(years=1)]
    bench_vol = series.benchmark.reindex(window).pct_change().dropna().std() * math.sqrt(252) * 100
    assert risk["holdings"]["MKT"]["vol_1y"] == pytest.approx(bench_vol, rel=0.01)
    assert risk["holdings"]["LEV"]["vol_1y"] == pytest.approx(2 * bench_vol, rel=0.01)


def test_late_listing_without_enough_real_closes_gets_no_metrics():
    n = len(BENCH_RETURNS) + 1
    series = make_series(
        {"NEW": levels(BENCH_RETURNS), "OLD": levels(BENCH_RETURNS)},
        levels(BENCH_RETURNS),
        first_close={"NEW": n - MIN_POINTS},
    )

    risk = compute_risk(series, [])

    assert risk["holdings"]["NEW"] == {"beta": None, "vol_1y": None}
    assert risk["holdings"]["OLD"]["beta"] is not None


def test_portfolio_max_drawdown_over_last_year():
    flat = [100.0] * 200
    path = flat + [110.0, 120.0, 100.0, 90.0, 95.0]
    series = make_series({"A": path}, [100.0] * len(path))

    risk = compute_risk(series, [])

    assert risk["portfolio"]["max_drawdown_1y"] == pytest.approx(-25.0)
    # Flat benchmark: no beta, but volatility is still reported.
    assert risk["portfolio"]["beta"] is None
    assert risk["portfolio"]["vol_1y"] is not None


def test_short_history_has_no_portfolio_metrics():
    series = make_series({"A": levels(BENCH_RETURNS[:10])}, levels(BENCH_RETURNS[:10]))

    risk = compute_risk(series, [])

    assert risk["portfolio"] == {"beta": None, "vol_1y": None, "max_drawdown_1y": None}
    assert risk["holdings"]["A"] == {"beta": None, "vol_1y": None}


def test_no_series_still_reports_concentration():
    rows = [
        {"ticker": "A", "weight": 50.0, "sector": "Technology", "country": "United States"},
        {"ticker": "B", "weight": 30.0, "sector": "Technology", "country": "Finland"},
        {"ticker": "C", "weight": 15.0, "sector": "Financial Services", "country": "Finland"},
        {"ticker": "D", "weight": 5.0, "sector": None, "country": None},
        {"ticker": "E", "weight": None, "sector": "Energy", "country": "Norway"},
    ]

    risk = compute_risk(None, rows)

    assert risk["holdings"] == {}
    assert risk["portfolio"] == {"beta": None, "vol_1y": None, "max_drawdown_1y": None}
    assert risk["concentration"] == {
        "top3_weight": 95.0,
        "largest_sector": {"name": "Technology", "weight": 80.0},
        "largest_country": {"name": "United States", "weight": 50.0},
    }


def test_empty_concentration():
    assert compute_risk(None, [])["concentration"] == {
        "top3_weight": None,
        "largest_sector": None,
        "largest_country": None,
    }
