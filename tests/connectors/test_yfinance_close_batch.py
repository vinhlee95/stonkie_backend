import math

import pandas as pd

from connectors.yfinance_client import YFinanceClient, parse_close_batch

INDEX = pd.to_datetime(["2026-09-29", "2026-09-30", "2026-10-01"])
NAN = math.nan


def batch(columns: dict[str, list[float]]) -> pd.DataFrame:
    """Shape of yf.download(group_by="ticker"): (symbol, field) columns on one shared date index."""
    frame = pd.DataFrame(
        {(symbol, field): values for symbol, values in columns.items() for field in ("Open", "Close")}, index=INDEX
    )
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    return frame


def test_close_per_symbol_with_non_trading_days_dropped():
    result = parse_close_batch(batch({"AAPL": [1.0, NAN, 3.0], "NOKIA.HE": [4.0, 5.0, 6.0]}), ["AAPL", "NOKIA.HE"])

    assert result["AAPL"].to_dict() == {INDEX[0]: 1.0, INDEX[2]: 3.0}
    assert result["NOKIA.HE"].tolist() == [4.0, 5.0, 6.0]


def test_unknown_or_missing_symbols_are_omitted():
    result = parse_close_batch(batch({"AAPL": [1.0, 2.0, 3.0], "NOPE": [NAN, NAN, NAN]}), ["AAPL", "NOPE", "ABSENT"])

    assert list(result) == ["AAPL"]


def test_empty_or_flat_frame_returns_nothing():
    assert parse_close_batch(pd.DataFrame(), ["AAPL"]) == {}
    assert parse_close_batch(None, ["AAPL"]) == {}
    assert parse_close_batch(pd.DataFrame({"Close": [1.0]}), ["AAPL"]) == {}


def test_batch_download_requests_5y_unadjusted_daily_grouped_by_ticker(monkeypatch):
    calls = []

    def fake_download(symbols, **kwargs):
        calls.append((symbols, kwargs))
        return batch({"AAPL": [1.0, 2.0, 3.0]})

    monkeypatch.setattr("connectors.yfinance_client.yf.download", fake_download)

    result = YFinanceClient().get_close_history_batch(["AAPL"])

    assert result["AAPL"].tolist() == [1.0, 2.0, 3.0]  # single symbol still comes back grouped
    symbols, kwargs = calls[0]
    assert symbols == ["AAPL"]
    assert kwargs == {
        "period": "5y",
        "interval": "1d",
        "auto_adjust": False,
        "group_by": "ticker",
        "threads": True,
        "progress": False,
    }
