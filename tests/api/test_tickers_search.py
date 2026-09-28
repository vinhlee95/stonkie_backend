import pytest
from fastapi.testclient import TestClient

from api.tickers import get_yfinance_client
from main import app

SXR8_QUOTES = [
    {
        "symbol": "SXR8.DE",
        "shortname": "iShs VII-Core S&P 500 U.ETF   R",
        "longname": "iShares Core S&P 500 UCITS ETF USD (Acc)",
        "quoteType": "ETF",
        "exchDisp": "XETRA",
        "isYahooFinance": True,
    },
    {
        "symbol": "SXR8.SG",
        "shortname": "iShares Core S&P 500 UCITS ETF",
        "longname": None,
        "quoteType": "MUTUALFUND",
        "exchDisp": "Stuttgart",
        "isYahooFinance": True,
    },
    {"symbol": "AAPL", "shortname": "Apple Inc.", "quoteType": "EQUITY", "exchDisp": "NASDAQ", "isYahooFinance": True},
    {"symbol": "^GSPC", "shortname": "S&P 500", "quoteType": "INDEX", "exchDisp": "SNP", "isYahooFinance": True},
    {"symbol": "EURUSD=X", "shortname": "EUR/USD", "quoteType": "CURRENCY", "isYahooFinance": True},
    {"symbol": "XYZ", "shortname": "Not on Yahoo", "quoteType": "EQUITY", "isYahooFinance": False},
]


class FakeYFinanceClient:
    def __init__(self, result: list[dict] | Exception):
        self.result = result
        self.calls: list[str] = []

    def search(self, query: str) -> list[dict]:
        self.calls.append(query)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeRedis:
    def __init__(self):
        self.store: dict[str, bytes] = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value.encode() if isinstance(value, str) else value


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("connectors.cache.redis_client", fake)
    return fake


@pytest.fixture()
def make_client():
    def _make(fake: FakeYFinanceClient) -> TestClient:
        app.dependency_overrides[get_yfinance_client] = lambda: fake
        return TestClient(app)

    yield _make
    app.dependency_overrides.pop(get_yfinance_client, None)


def test_returns_holdable_yahoo_listings(make_client):
    client = make_client(FakeYFinanceClient(SXR8_QUOTES))

    res = client.get("/api/tickers/search", params={"q": "SXR8"})

    assert res.status_code == 200
    assert res.json() == {
        "data": [
            {"symbol": "SXR8.DE", "name": "iShares Core S&P 500 UCITS ETF USD (Acc)", "exchange": "XETRA"},
            {"symbol": "SXR8.SG", "name": "iShares Core S&P 500 UCITS ETF", "exchange": "Stuttgart"},
            {"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ"},
        ]
    }


def test_second_request_served_from_cache(make_client):
    fake = FakeYFinanceClient(SXR8_QUOTES)
    client = make_client(fake)

    client.get("/api/tickers/search", params={"q": "sxr8"})
    res = client.get("/api/tickers/search", params={"q": " SXR8 "})

    assert res.status_code == 200
    assert len(res.json()["data"]) == 3
    assert fake.calls == ["sxr8"]


def test_empty_results_are_not_an_error(make_client):
    client = make_client(FakeYFinanceClient([]))
    res = client.get("/api/tickers/search", params={"q": "zzzz"})
    assert res.status_code == 200
    assert res.json() == {"data": []}


@pytest.mark.parametrize("params", [{}, {"q": ""}, {"q": "   "}, {"q": "x" * 65}])
def test_rejects_missing_blank_or_long_query(make_client, params):
    fake = FakeYFinanceClient(SXR8_QUOTES)
    client = make_client(fake)
    assert client.get("/api/tickers/search", params=params).status_code == 422
    assert fake.calls == []


def test_yahoo_failure_returns_502_and_is_not_cached(make_client, fake_redis):
    client = make_client(FakeYFinanceClient(RuntimeError("yahoo down")))
    res = client.get("/api/tickers/search", params={"q": "SXR8"})
    assert res.status_code == 502
    assert fake_redis.store == {}
