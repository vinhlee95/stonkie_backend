from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.quotes import get_yfinance_client
from main import app

NY_TZ = ZoneInfo("America/New_York")
BERLIN_TZ = ZoneInfo("Europe/Berlin")


def last_completed_business_day(tz: ZoneInfo) -> datetime:
    candidate = datetime.now(tz) - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def make_history(closes: list[float], tz: ZoneInfo, last_date: datetime | None = None) -> pd.DataFrame:
    end = (last_date or last_completed_business_day(tz)).strftime("%Y-%m-%d")
    index = pd.bdate_range(end=end, periods=len(closes), tz=str(tz))
    return pd.DataFrame({"Close": closes}, index=index)


class FakeYFinanceClient:
    def __init__(
        self,
        histories: dict[str, pd.DataFrame | Exception],
        currencies: dict[str, str] | None = None,
    ):
        self.histories = histories
        self.currencies = currencies or {}
        self.calls: list[str] = []

    def get_daily_history(self, ticker: str) -> tuple[pd.DataFrame, str | None]:
        self.calls.append(ticker)
        result = self.histories[ticker]
        if isinstance(result, Exception):
            raise result
        return result, self.currencies.get(ticker)


class FakeRedis:
    def __init__(self):
        self.store: dict[str, tuple[str, int]] = {}

    def get(self, key):
        entry = self.store.get(key)
        return entry[0].encode() if entry else None

    def setex(self, key, ttl, value):
        self.store[key] = (value, ttl)

    def ttl(self, key):
        entry = self.store.get(key)
        return entry[1] if entry else -2


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


def test_returns_daily_change_for_single_ticker(make_client):
    history = make_history([290.00, 291.58, 295.28], tz=NY_TZ)
    client = make_client(FakeYFinanceClient({"AAPL": history}))

    response = client.get("/api/quotes/price-changes?tickers=AAPL")

    assert response.status_code == 200
    quote = response.json()["quotes"]["AAPL"]
    assert quote["close"] == 295.28
    assert quote["prev_close"] == 291.58
    assert quote["change"] == 3.7
    assert quote["change_percent"] == 1.27
    assert quote["trading_date"] == history.index[-1].date().isoformat()


def test_batch_tickers_deduped_and_uppercased(make_client):
    fake = FakeYFinanceClient(
        {
            "AAPL": make_history([290.00, 295.28], tz=NY_TZ),
            "MSFT": make_history([397.36, 387.35], tz=NY_TZ),
        }
    )
    client = make_client(fake)

    response = client.get("/api/quotes/price-changes?tickers=aapl,MSFT,AAPL")

    assert response.status_code == 200
    assert sorted(response.json()["quotes"]) == ["AAPL", "MSFT"]
    assert sorted(fake.calls) == ["AAPL", "MSFT"]


def test_empty_tickers_rejected(make_client):
    client = make_client(FakeYFinanceClient({}))

    assert client.get("/api/quotes/price-changes").status_code == 422
    assert client.get("/api/quotes/price-changes?tickers=").status_code == 422
    assert client.get("/api/quotes/price-changes?tickers=,,").status_code == 422


def test_more_than_50_tickers_rejected(make_client):
    client = make_client(FakeYFinanceClient({}))
    tickers = ",".join(f"T{i}" for i in range(51))

    assert client.get(f"/api/quotes/price-changes?tickers={tickers}").status_code == 422


def test_failed_ticker_omitted(make_client):
    fake = FakeYFinanceClient(
        {
            "AAPL": make_history([290.00, 295.28], tz=NY_TZ),
            "BAD": RuntimeError("yfinance unavailable"),
        }
    )
    client = make_client(fake)

    response = client.get("/api/quotes/price-changes?tickers=AAPL,BAD")

    assert response.status_code == 200
    assert list(response.json()["quotes"]) == ["AAPL"]


def _freeze_now(monkeypatch, local_dt: datetime) -> None:
    frozen = local_dt.astimezone(UTC)
    monkeypatch.setattr("services.price_change._utcnow", lambda: frozen)


@pytest.mark.parametrize("tz", [NY_TZ, BERLIN_TZ], ids=["us", "eu"])
def test_inprogress_bar_excluded_uses_prior_completed_days(make_client, monkeypatch, tz):
    # Thursday 2026-06-11, 14:00 local — session still open, last bar is today's
    _freeze_now(monkeypatch, datetime(2026, 6, 11, 14, 0, tzinfo=tz))
    history = make_history([100.0, 110.0, 121.0], tz=tz, last_date=datetime(2026, 6, 11))
    client = make_client(FakeYFinanceClient({"TICK": history}))

    quote = client.get("/api/quotes/price-changes?tickers=TICK").json()["quotes"]["TICK"]

    assert quote["close"] == 110.0
    assert quote["prev_close"] == 100.0
    assert quote["change_percent"] == 10.0
    assert quote["trading_date"] == "2026-06-10"


def test_todays_bar_kept_after_session_end(make_client, monkeypatch):
    _freeze_now(monkeypatch, datetime(2026, 6, 11, 19, 0, tzinfo=NY_TZ))
    history = make_history([100.0, 110.0, 121.0], tz=NY_TZ, last_date=datetime(2026, 6, 11))
    client = make_client(FakeYFinanceClient({"TICK": history}))

    quote = client.get("/api/quotes/price-changes?tickers=TICK").json()["quotes"]["TICK"]

    assert quote["close"] == 121.0
    assert quote["prev_close"] == 110.0
    assert quote["trading_date"] == "2026-06-11"


def test_insufficient_history_omitted(make_client):
    fake = FakeYFinanceClient(
        {
            "ONEBAR": make_history([100.0], tz=NY_TZ),
            "EMPTY": pd.DataFrame({"Close": []}, index=pd.DatetimeIndex([], tz=str(NY_TZ))),
            "AAPL": make_history([290.00, 295.28], tz=NY_TZ),
        }
    )
    client = make_client(fake)

    response = client.get("/api/quotes/price-changes?tickers=ONEBAR,EMPTY,AAPL")

    assert response.status_code == 200
    assert list(response.json()["quotes"]) == ["AAPL"]


def test_second_request_served_from_cache(make_client, fake_redis):
    fake = FakeYFinanceClient({"AAPL": make_history([290.00, 295.28], tz=NY_TZ)})
    client = make_client(fake)

    first = client.get("/api/quotes/price-changes?tickers=AAPL")
    second = client.get("/api/quotes/price-changes?tickers=AAPL")

    assert first.json() == second.json()
    assert fake.calls == ["AAPL"]
    assert fake_redis.ttl("price_change:AAPL") == 6 * 3600


def test_currency_included_when_available(make_client):
    fake = FakeYFinanceClient(
        {
            "VWCE.DE": make_history([158.0, 159.56], tz=BERLIN_TZ),
            "AAPL": make_history([290.00, 295.28], tz=NY_TZ),
        },
        currencies={"VWCE.DE": "EUR"},
    )
    client = make_client(fake)

    quotes = client.get("/api/quotes/price-changes?tickers=VWCE.DE,AAPL").json()["quotes"]

    assert quotes["VWCE.DE"]["currency"] == "EUR"
    assert quotes["AAPL"]["currency"] is None
