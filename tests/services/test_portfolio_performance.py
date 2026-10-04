from datetime import UTC, date, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from connectors.yfinance_client import LiveQuoteDto
from services import price_history
from services.portfolio_performance import get_performance
from tests.api.test_quotes_price_changes import FakeRedis, FakeYFinanceClient


def closes(values: dict[str, float]) -> pd.Series:
    return pd.Series(list(values.values()), index=pd.to_datetime(list(values)), dtype=float)


def live(currency: str | None) -> LiveQuoteDto:
    return LiveQuoteDto(
        price=1.0,
        prev_close=1.0,
        currency=currency,
        market_time=datetime(2026, 10, 1, 18, 0, tzinfo=UTC),
        trading_date=date(2026, 10, 1),
    )


class FakePortfolio:
    def __init__(self, holdings: dict[str, float]):
        self.holdings = [SimpleNamespace(ticker=t, shares=s) for t, s in holdings.items()]

    def list_holdings(self, user_id: str):
        return self.holdings


# Mon-Wed. 2026-09-29 is a "US holiday": no AAPL / ^GSPC bar, Helsinki trades.
HISTORIES = {
    "NOKIA.HE": closes({"2026-09-28": 4.0, "2026-09-29": 5.0, "2026-09-30": 5.0}),
    "AAPL": closes({"2026-09-28": 100.0, "2026-09-30": 120.0}),
    "VOD.L": closes({"2026-09-28": 100.0, "2026-09-29": 100.0, "2026-09-30": 200.0}),
    "^GSPC": closes({"2026-09-28": 5000.0, "2026-09-30": 5200.0}),
    # FX trades on a Saturday too; that date must not become a chart point.
    "USDEUR=X": closes({"2026-09-26": 0.95, "2026-09-28": 0.9, "2026-09-29": 0.9, "2026-09-30": 0.8}),
    "GBPEUR=X": closes({"2026-09-28": 1.2, "2026-09-29": 1.2, "2026-09-30": 1.2}),
}
LIVE = {"NOKIA.HE": live("EUR"), "AAPL": live("USD"), "VOD.L": live("GBp")}


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    monkeypatch.setattr("connectors.cache.redis_client", FakeRedis())


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(price_history, "_utcnow", lambda: datetime(2026, 10, 2, 12, 0, tzinfo=UTC))


def run(holdings: dict[str, float], histories=HISTORIES, live_quotes=LIVE) -> tuple[dict, FakeYFinanceClient]:
    fake = FakeYFinanceClient({}, live_quotes=live_quotes, close_histories=histories)
    return get_performance("user-1", FakePortfolio(holdings), fake), fake


def by_date(result: dict) -> dict[str, tuple[float, float]]:
    return {p["date"]: (p["portfolio_value"], p["benchmark_value"]) for p in result["points"]}


def test_values_mixed_currency_holdings_and_benchmark_in_eur():
    result, _ = run({"NOKIA.HE": 10, "AAPL": 2, "VOD.L": 100})

    assert result["base_currency"] == "EUR"
    assert result["excluded"] == []
    points = by_date(result)
    # 10 x 4 EUR + 2 x 100 USD x 0.9 + 100 x 100p / 100 x 1.2
    assert points["2026-09-28"] == (pytest.approx(340.0), pytest.approx(4500.0))
    # 10 x 5 + 2 x 120 x 0.8 + 100 x 2 x 1.2
    assert points["2026-09-30"] == (pytest.approx(482.0), pytest.approx(4160.0))


def test_dates_are_union_of_holding_and_benchmark_sessions_with_gaps_filled():
    result, _ = run({"NOKIA.HE": 10, "AAPL": 2})

    assert [p["date"] for p in result["points"]] == ["2026-09-28", "2026-09-29", "2026-09-30"]
    # US holiday: AAPL and ^GSPC carry Monday's close.
    assert by_date(result)["2026-09-29"] == (pytest.approx(10 * 5 + 2 * 100 * 0.9), pytest.approx(5000 * 0.9))


def test_late_listing_is_flat_at_first_close_before_it_trades():
    histories = {**HISTORIES, "NEW.HE": closes({"2026-09-30": 10.0})}
    result, _ = run({"NEW.HE": 1}, histories, {"NEW.HE": live("EUR")})

    assert [p["portfolio_value"] for p in result["points"]] == [10.0, 10.0]


def test_holding_whose_history_stops_early_is_carried_flat():
    histories = {**HISTORIES, "OLD.HE": closes({"2026-09-28": 7.0})}
    result, _ = run({"OLD.HE": 1}, histories, {"OLD.HE": live("EUR")})

    assert [p["portfolio_value"] for p in result["points"]] == [7.0, 7.0]


def test_all_eur_portfolio_still_converts_benchmark_on_its_own_dates():
    result, fake = run({"NOKIA.HE": 10}, live_quotes={"NOKIA.HE": live("EUR")})

    assert sorted(fake.batch_calls[0]) == ["NOKIA.HE", "USDEUR=X", "^GSPC"]
    # Helsinki trades on the US holiday: ^GSPC carries Monday's close at that day's FX.
    assert by_date(result)["2026-09-29"] == (pytest.approx(50.0), pytest.approx(5000 * 0.9))


def test_holdings_without_currency_history_or_fx_are_excluded():
    histories = {k: v for k, v in HISTORIES.items() if k != "GBPEUR=X"}
    live_quotes = {**LIVE, "NOCCY": live(None), "NOHIST": live("EUR")}

    result, _ = run({"NOKIA.HE": 10, "VOD.L": 100, "NOCCY": 1, "NOHIST": 1}, histories, live_quotes)

    assert result["excluded"] == ["NOCCY", "NOHIST", "VOD.L"]
    assert by_date(result)["2026-09-28"][0] == pytest.approx(40.0)


def test_fx_and_benchmark_fetched_once_with_holdings():
    _, fake = run({"NOKIA.HE": 10, "AAPL": 2, "VOD.L": 100})

    assert len(fake.batch_calls) == 1
    assert sorted(fake.batch_calls[0]) == sorted(["NOKIA.HE", "AAPL", "VOD.L", "^GSPC", "USDEUR=X", "GBPEUR=X"])


def test_no_benchmark_history_returns_no_points():
    histories = {k: v for k, v in HISTORIES.items() if k != "^GSPC"}

    result, _ = run({"NOKIA.HE": 10}, histories)

    assert result["points"] == []
    assert result["excluded"] == []


def test_no_usd_fx_for_benchmark_returns_no_points():
    histories = {k: v for k, v in HISTORIES.items() if k != "USDEUR=X"}

    result, _ = run({"NOKIA.HE": 10}, histories, {"NOKIA.HE": live("EUR")})

    assert result == {"base_currency": "EUR", "points": [], "excluded": []}


def test_everything_excluded_returns_no_points():
    result, _ = run({"NOCCY": 1}, live_quotes={"NOCCY": live(None)})

    assert result == {"base_currency": "EUR", "points": [], "excluded": ["NOCCY"]}


def test_empty_portfolio_makes_no_yahoo_calls():
    result, fake = run({})

    assert result == {"base_currency": "EUR", "points": [], "excluded": []}
    assert fake.batch_calls == [] and fake.live_calls == []
