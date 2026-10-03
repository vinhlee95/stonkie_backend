import json
from datetime import UTC, datetime

import pandas as pd
import pytest

from services import price_history
from services.price_history import PRICE_HISTORY_TTL_SECONDS, get_close_histories
from tests.api.test_quotes_price_changes import FakeRedis, FakeYFinanceClient


def closes(values: dict[str, float]) -> pd.Series:
    return pd.Series(list(values.values()), index=pd.to_datetime(list(values)), dtype=float)


AAPL = closes({"2026-09-30": 250.0, "2026-10-01": 255.0})
GSPC = closes({"2026-09-30": 6600.0, "2026-10-01": 6650.0})


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("connectors.cache.redis_client", fake)
    return fake


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(price_history, "_utcnow", lambda: datetime(2026, 10, 2, 15, 0, tzinfo=UTC))


def test_fetches_misses_in_one_batch_and_caches_each_symbol(fake_redis):
    fake = FakeYFinanceClient({}, close_histories={"AAPL": AAPL, "^GSPC": GSPC})

    result = get_close_histories(["AAPL", "^GSPC"], fake)

    assert result == {
        "AAPL": {"2026-09-30": 250.0, "2026-10-01": 255.0},
        "^GSPC": {"2026-09-30": 6600.0, "2026-10-01": 6650.0},
    }
    assert fake.batch_calls == [["AAPL", "^GSPC"]]
    assert fake_redis.ttl("price_history:AAPL:5y") == PRICE_HISTORY_TTL_SECONDS == 12 * 3600


def test_second_call_served_from_cache():
    fake = FakeYFinanceClient({}, close_histories={"AAPL": AAPL})
    first = get_close_histories(["AAPL"], fake)

    assert get_close_histories(["AAPL"], fake) == first
    assert fake.batch_calls == [["AAPL"]]


def test_partial_miss_downloads_only_missing_symbols():
    fake = FakeYFinanceClient({}, close_histories={"AAPL": AAPL, "^GSPC": GSPC})
    get_close_histories(["AAPL"], fake)

    result = get_close_histories(["AAPL", "^GSPC", "AAPL"], fake)

    assert set(result) == {"AAPL", "^GSPC"}
    assert fake.batch_calls == [["AAPL"], ["^GSPC"]]


def test_todays_possibly_trading_bar_is_dropped():
    fake = FakeYFinanceClient({}, close_histories={"AAPL": closes({"2026-10-01": 255.0, "2026-10-02": 258.0})})

    assert get_close_histories(["AAPL"], fake) == {"AAPL": {"2026-10-01": 255.0}}


def test_invalid_closes_are_skipped():
    series = closes({"2026-09-29": 0.0, "2026-09-30": float("nan"), "2026-10-01": 255.0})
    fake = FakeYFinanceClient({}, close_histories={"AAPL": series})

    assert get_close_histories(["AAPL"], fake) == {"AAPL": {"2026-10-01": 255.0}}


def test_symbol_without_usable_history_is_omitted_and_not_cached(fake_redis):
    fake = FakeYFinanceClient(
        {}, close_histories={"AAPL": AAPL, "TODAY": closes({"2026-10-02": 1.0}), "ZERO": closes({"2026-10-01": 0.0})}
    )

    assert set(get_close_histories(["AAPL", "TODAY", "ZERO", "NONE"], fake)) == {"AAPL"}
    for symbol in ("TODAY", "ZERO", "NONE"):
        assert fake_redis.ttl(f"price_history:{symbol}:5y") == -2


def test_failed_download_returns_cached_symbols_only(fake_redis):
    get_close_histories(["AAPL"], FakeYFinanceClient({}, close_histories={"AAPL": AAPL}))
    fake = FakeYFinanceClient({}, close_histories=RuntimeError("yahoo down"))

    assert set(get_close_histories(["AAPL", "^GSPC"], fake)) == {"AAPL"}
    assert fake.batch_calls == [["^GSPC"]]
    assert fake_redis.ttl("price_history:^GSPC:5y") == -2


def test_all_cached_makes_no_download():
    get_close_histories(["AAPL"], FakeYFinanceClient({}, close_histories={"AAPL": AAPL}))
    fake = FakeYFinanceClient({})

    get_close_histories(["AAPL"], fake)

    assert fake.batch_calls == []


@pytest.mark.parametrize(
    "cached",
    [
        [1, 2],
        {},
        {"closes": {}},
        {"closes": [250.0]},
        {"closes": {"not-a-date": 250.0}},
        *({"closes": {"2026-10-01": bad}} for bad in ("250", 0, -1, None, True)),
    ],
)
def test_malformed_cache_entry_is_refetched(cached, fake_redis):
    fake_redis.setex("price_history:AAPL:5y", PRICE_HISTORY_TTL_SECONDS, json.dumps(cached))
    fake = FakeYFinanceClient({}, close_histories={"AAPL": AAPL})

    assert get_close_histories(["AAPL"], fake) == {"AAPL": {"2026-09-30": 250.0, "2026-10-01": 255.0}}
    assert fake.batch_calls == [["AAPL"]]
