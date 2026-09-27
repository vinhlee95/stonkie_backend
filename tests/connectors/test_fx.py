from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from connectors import fx as fx_module
from connectors.fx import FxConnector
from connectors.yfinance_client import LiveQuoteDto
from tests.api.test_quotes_price_changes import FakeRedis, FakeYFinanceClient

NOW = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)  # a Friday afternoon


def history(closes: list[float], last_day: datetime) -> pd.DataFrame:
    index = pd.DatetimeIndex([last_day - timedelta(days=i) for i in reversed(range(len(closes)))])
    return pd.DataFrame({"Close": closes}, index=index.normalize())


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("connectors.cache.redis_client", fake)
    monkeypatch.setattr(fx_module, "_utcnow", lambda: NOW)
    return fake


def test_same_currency_is_one():
    assert FxConnector(FakeYFinanceClient({})).get_rate("EUR", "EUR") == 1.0


def test_uses_last_completed_close_not_forming_bar():
    client = FakeYFinanceClient({"USDEUR=X": history([0.90, 0.85, 0.80], last_day=NOW)})

    assert FxConnector(client).get_rate("USD", "EUR") == pytest.approx(0.85)


def test_uses_latest_close_when_no_bar_for_today():
    client = FakeYFinanceClient({"USDEUR=X": history([0.90, 0.85], last_day=NOW - timedelta(days=1))})

    assert FxConnector(client).get_rate("USD", "EUR") == pytest.approx(0.85)


def test_rate_is_cached():
    client = FakeYFinanceClient({"USDEUR=X": history([0.90, 0.85], last_day=NOW - timedelta(days=1))})
    connector = FxConnector(client)

    connector.get_rate("USD", "EUR")
    connector.get_rate("USD", "EUR")

    assert client.calls == ["USDEUR=X"]


def test_fetch_failure_returns_none():
    client = FakeYFinanceClient({"USDEUR=X": RuntimeError("down")})

    assert FxConnector(client).get_rate("USD", "EUR") is None


def live(price: float) -> LiveQuoteDto:
    return LiveQuoteDto(price=price, prev_close=price, currency="EUR", market_time=NOW, trading_date=NOW.date())


def test_live_same_currency_is_one():
    client = FakeYFinanceClient({})

    assert FxConnector(client).get_live_rate("EUR", "EUR") == 1.0
    assert client.live_calls == []


def test_live_rate_uses_live_quote_and_is_cached(fake_env):
    client = FakeYFinanceClient({}, live_quotes={"USDEUR=X": live(0.877)})
    connector = FxConnector(client)

    assert connector.get_live_rate("USD", "EUR") == pytest.approx(0.877)
    assert connector.get_live_rate("USD", "EUR") == pytest.approx(0.877)
    assert client.live_calls == ["USDEUR=X"]
    assert fake_env.ttl("fx_live:USDEUR") == 300


@pytest.mark.parametrize("failure", [RuntimeError("down"), None])
def test_live_failure_falls_back_to_daily_rate(failure, fake_env):
    client = FakeYFinanceClient(
        {"USDEUR=X": history([0.90, 0.85], last_day=NOW - timedelta(days=1))},
        live_quotes={"USDEUR=X": failure},
    )

    assert FxConnector(client).get_live_rate("USD", "EUR") == pytest.approx(0.85)
    assert fake_env.ttl("fx_live:USDEUR") == -2
