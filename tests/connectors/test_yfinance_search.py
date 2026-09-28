from connectors import yfinance_client
from connectors.yfinance_client import YFinanceClient


def test_search_raises_on_errors_and_skips_news(monkeypatch):
    calls = []

    class FakeSearch:
        def __init__(self, query, **kwargs):
            calls.append((query, kwargs))
            self.quotes = [{"symbol": "SXR8.DE"}]

    monkeypatch.setattr(yfinance_client.yf, "Search", FakeSearch)

    assert YFinanceClient().search("sxr8") == [{"symbol": "SXR8.DE"}]
    query, kwargs = calls[0]
    assert query == "sxr8"
    # Without raise_errors yfinance returns empty quotes on failure, which would be cached as "no matches".
    assert kwargs["raise_errors"] is True
    assert kwargs["news_count"] == 0
    assert kwargs["timeout"] <= 3
