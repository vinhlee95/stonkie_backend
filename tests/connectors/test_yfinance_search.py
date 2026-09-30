import json

import pytest
from yfinance.exceptions import YFDataException, YFRateLimitError

from connectors import yfinance_client
from connectors.yfinance_client import TickerSearchQuoteDto, YahooSearchUnavailableError, YFinanceClient


def test_search_raises_on_errors_and_skips_news(monkeypatch):
    calls = []

    class FakeSearch:
        def __init__(self, query, **kwargs):
            calls.append((query, kwargs))
            self.quotes = [{"symbol": "SXR8.DE"}]

    monkeypatch.setattr(yfinance_client.yf, "Search", FakeSearch)

    assert YFinanceClient().search("sxr8") == [
        TickerSearchQuoteDto(symbol="SXR8.DE", name=None, exchange=None, quote_type=None, is_yahoo_finance=False)
    ]
    query, kwargs = calls[0]
    assert query == "sxr8"
    # Without raise_errors yfinance returns empty quotes on failure, which would be cached as "no matches".
    assert kwargs["raise_errors"] is True
    assert kwargs["news_count"] == 0
    assert kwargs["timeout"] <= 3


def _patch_search(monkeypatch, quotes=None, error: Exception | None = None):
    class FakeSearch:
        def __init__(self, query, **kwargs):
            if error is not None:
                raise error
            self.quotes = quotes

    monkeypatch.setattr(yfinance_client.yf, "Search", FakeSearch)


def test_search_maps_quotes_to_dtos_and_drops_symbolless(monkeypatch):
    _patch_search(
        monkeypatch,
        quotes=[
            {
                "symbol": "SXR8.DE",
                "longname": "iShares Core S&P 500",
                "shortname": "iShs Core",
                "exchDisp": "XETRA",
                "quoteType": "ETF",
                "isYahooFinance": True,
            },
            {"symbol": "AAPL", "shortname": "Apple Inc.", "quoteType": "EQUITY"},
            {"shortname": "no symbol"},
        ],
    )

    assert YFinanceClient().search("x") == [
        TickerSearchQuoteDto("SXR8.DE", "iShares Core S&P 500", "XETRA", "ETF", True),
        TickerSearchQuoteDto("AAPL", "Apple Inc.", None, "EQUITY", False),
    ]


class Timeout(Exception):
    """Stands in for curl_cffi/requests Timeout (matched by name)."""


@pytest.mark.parametrize(
    "error",
    [
        YFDataException("down"),
        YFRateLimitError(),
        TimeoutError(),
        ConnectionError(),
        Timeout(),
        json.JSONDecodeError("bad", "<html>", 0),
    ],
)
def test_search_outage_errors_become_unavailable(monkeypatch, error):
    _patch_search(monkeypatch, error=error)
    with pytest.raises(YahooSearchUnavailableError):
        YFinanceClient().search("x")


def test_search_other_errors_propagate_unchanged(monkeypatch):
    _patch_search(monkeypatch, error=ValueError("bad query"))
    with pytest.raises(ValueError):
        YFinanceClient().search("x")
