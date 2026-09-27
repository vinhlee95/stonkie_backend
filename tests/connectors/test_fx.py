from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from connectors import fx as fx_module
from connectors.fx import FxConnector
from tests.api.test_quotes_price_changes import FakeRedis, FakeYFinanceClient

NOW = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)  # a Friday afternoon


def history(closes: list[float], last_day: datetime) -> pd.DataFrame:
    index = pd.DatetimeIndex([last_day - timedelta(days=i) for i in reversed(range(len(closes)))])
    return pd.DataFrame({"Close": closes}, index=index.normalize())


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setattr("connectors.cache.redis_client", FakeRedis())
    monkeypatch.setattr(fx_module, "_utcnow", lambda: NOW)


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
