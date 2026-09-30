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
        self.ttls: dict[str, int] = {}

    def get(self, key):
        return self.store.get(key)

    def mget(self, keys):
        return [self.store.get(k) for k in keys]

    def setex(self, key, ttl, value):
        self.store[key] = value.encode() if isinstance(value, str) else value
        self.ttls[key] = ttl


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


def test_second_request_served_from_cache(make_client, fake_redis):
    fake = FakeYFinanceClient(SXR8_QUOTES)
    client = make_client(fake)

    client.get("/api/tickers/search", params={"q": "sxr8"})
    res = client.get("/api/tickers/search", params={"q": " SXR8 "})

    assert res.status_code == 200
    assert len(res.json()["data"]) == 3
    assert fake.calls == ["sxr8"]
    assert fake_redis.ttls["ticker_search:sxr8"] == 3600


def test_empty_results_are_not_an_error_and_not_cached(make_client, fake_redis):
    fake = FakeYFinanceClient([])
    client = make_client(fake)

    res = client.get("/api/tickers/search", params={"q": "zzzz"})
    client.get("/api/tickers/search", params={"q": "zzzz"})

    assert res.status_code == 200
    assert res.json() == {"data": []}
    assert fake.calls == ["zzzz", "zzzz"]
    assert fake_redis.store == {}


@pytest.mark.parametrize("params", [{}, {"q": ""}, {"q": "   "}, {"q": "x" * 65}])
def test_rejects_missing_blank_or_long_query(make_client, params):
    fake = FakeYFinanceClient(SXR8_QUOTES)
    client = make_client(fake)
    assert client.get("/api/tickers/search", params=params).status_code == 422
    assert fake.calls == []


def test_maps_missing_fields_and_drops_symbolless_quotes(make_client):
    quotes = [
        {"symbol": "nokia.he", "quoteType": "EQUITY", "isYahooFinance": True},
        {"shortname": "No symbol", "quoteType": "EQUITY", "isYahooFinance": True},
    ]
    client = make_client(FakeYFinanceClient(quotes))
    res = client.get("/api/tickers/search", params={"q": "nokia"})
    assert res.json() == {"data": [{"symbol": "NOKIA.HE", "name": "NOKIA.HE", "exchange": None}]}


def test_yahoo_failure_returns_502_without_caching_results(make_client, fake_redis):
    client = make_client(FakeYFinanceClient(RuntimeError("yahoo down")))
    res = client.get("/api/tickers/search", params={"q": "SXR8"})
    assert res.status_code == 502
    assert "ticker_search:sxr8" not in fake_redis.store


def test_yahoo_failure_short_circuits_later_searches(make_client, fake_redis):
    fake = FakeYFinanceClient(RuntimeError("yahoo down"))
    client = make_client(fake)

    client.get("/api/tickers/search", params={"q": "SXR8"})
    res = client.get("/api/tickers/search", params={"q": "apple"})

    assert res.status_code == 502
    assert fake.calls == ["sxr8"]
    assert fake_redis.ttls["ticker_search_outage:yahoo"] == 60


def test_query_cannot_collide_with_outage_key(make_client, fake_redis):
    fake = FakeYFinanceClient(SXR8_QUOTES)
    client = make_client(fake)

    client.get("/api/tickers/search", params={"q": "__yahoo_down"})
    res = client.get("/api/tickers/search", params={"q": "apple"})

    assert res.status_code == 200
    assert fake.calls == ["__yahoo_down", "apple"]
    assert "ticker_search_outage:yahoo" not in fake_redis.store


def test_cached_query_still_served_during_outage(make_client):
    fake = FakeYFinanceClient(SXR8_QUOTES)
    client = make_client(fake)
    client.get("/api/tickers/search", params={"q": "sxr8"})

    fake.result = RuntimeError("yahoo down")
    assert client.get("/api/tickers/search", params={"q": "apple"}).status_code == 502
    res = client.get("/api/tickers/search", params={"q": "sxr8"})

    assert res.status_code == 200
    assert len(res.json()["data"]) == 3
    assert fake.calls == ["sxr8", "apple"]
