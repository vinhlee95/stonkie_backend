import math

import pandas as pd
import pytest
from yfinance.exceptions import YFPricesMissingError

from connectors import yfinance_client
from connectors.yfinance_client import YFinanceClient

INDEX = pd.to_datetime(["2026-09-29", "2026-09-30", "2026-10-01"]).tz_localize("America/New_York")
NAN = math.nan


class FakeTicker:
    """Stands in for yf.Ticker: history() returns a frame or raises, per symbol."""

    calls: list[tuple[str, dict]] = []
    results: dict[str, pd.DataFrame | Exception] = {}

    def __init__(self, symbol: str):
        self.symbol = symbol

    def history(self, **kwargs):
        FakeTicker.calls.append((self.symbol, kwargs))
        result = FakeTicker.results[self.symbol]
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture(autouse=True)
def fake_ticker(monkeypatch):
    FakeTicker.calls = []
    FakeTicker.results = {}
    monkeypatch.setattr(yfinance_client.yf, "Ticker", FakeTicker)
    return FakeTicker


def frame(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"Open": closes, "Close": closes}, index=INDEX)


def test_fetches_each_symbol_once_for_5y_unadjusted_daily_bars(fake_ticker):
    fake_ticker.results = {"AAPL": frame([1.0, NAN, 3.0]), "NOKIA.HE": frame([4.0, 5.0, 6.0])}

    batch = YFinanceClient().get_close_history_batch(["AAPL", "NOKIA.HE"])

    assert batch.closes["AAPL"].to_dict() == {INDEX[0]: 1.0, INDEX[2]: 3.0}  # non-trading NaN dropped
    assert batch.closes["NOKIA.HE"].tolist() == [4.0, 5.0, 6.0]
    assert batch.failed == []
    assert sorted(symbol for symbol, _ in fake_ticker.calls) == ["AAPL", "NOKIA.HE"]
    _, kwargs = fake_ticker.calls[0]
    assert {k: kwargs[k] for k in ("period", "interval", "auto_adjust")} == {
        "period": "5y",
        "interval": "1d",
        "auto_adjust": False,
    }


def test_symbol_without_prices_is_omitted_not_failed(fake_ticker):
    fake_ticker.results = {
        "AAPL": frame([1.0, 2.0, 3.0]),
        "NOPE": YFPricesMissingError("NOPE", ""),
        "EMPTY": frame([NAN, NAN, NAN]),
        "NOCLOSE": pd.DataFrame({"Open": [1.0, 2.0, 3.0]}, index=INDEX),
    }

    batch = YFinanceClient().get_close_history_batch(["AAPL", "NOPE", "EMPTY", "NOCLOSE"])

    assert list(batch.closes) == ["AAPL"]
    assert batch.failed == []


def test_fetch_errors_are_reported_as_failed_for_retry(fake_ticker):
    fake_ticker.results = {"AAPL": frame([1.0, 2.0, 3.0]), "^GSPC": TimeoutError("read timed out")}

    batch = YFinanceClient().get_close_history_batch(["AAPL", "^GSPC"])

    assert list(batch.closes) == ["AAPL"]
    assert batch.failed == ["^GSPC"]


def test_no_symbols_makes_no_requests(fake_ticker):
    batch = YFinanceClient().get_close_history_batch([])

    assert batch.closes == {} and batch.failed == []
    assert fake_ticker.calls == []
