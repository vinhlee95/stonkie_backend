from datetime import UTC, date, datetime

import pytest

from connectors.yfinance_client import LiveQuoteDto
from services.portfolio.live_quote import LIVE_QUOTE_TTL_SECONDS, get_live_quotes
from tests.api.test_quotes_price_changes import FakeRedis, FakeYFinanceClient

AAPL = LiveQuoteDto(
    price=210.5,
    prev_close=200.0,
    currency="USD",
    market_time=datetime(2026, 9, 25, 18, 30, tzinfo=UTC),
    trading_date=date(2026, 9, 25),
)
MSFT = LiveQuoteDto(
    price=400.0,
    prev_close=410.0,
    currency="USD",
    market_time=datetime(2026, 9, 25, 18, 31, tzinfo=UTC),
    trading_date=date(2026, 9, 25),
)


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("connectors.cache.redis_client", fake)
    return fake


def test_fetches_live_quote_for_each_ticker():
    fake = FakeYFinanceClient({}, live_quotes={"AAPL": AAPL, "MSFT": MSFT})
    assert get_live_quotes(["AAPL", "MSFT"], fake) == {"AAPL": AAPL, "MSFT": MSFT}
    assert sorted(fake.live_calls) == ["AAPL", "MSFT"]


def test_second_call_served_from_cache_with_short_ttl(fake_redis):
    fake = FakeYFinanceClient({}, live_quotes={"AAPL": AAPL})
    get_live_quotes(["AAPL"], fake)
    assert get_live_quotes(["AAPL"], fake) == {"AAPL": AAPL}
    assert fake.live_calls == ["AAPL"]
    assert fake_redis.ttl("live_quote:AAPL") == LIVE_QUOTE_TTL_SECONDS == 300


def test_failed_or_missing_ticker_omitted_and_not_cached(fake_redis):
    fake = FakeYFinanceClient({}, live_quotes={"AAPL": AAPL, "BAD": RuntimeError("yahoo down")})
    assert get_live_quotes(["AAPL", "BAD", "NONE"], fake) == {"AAPL": AAPL}
    assert fake_redis.ttl("live_quote:BAD") == -2
    assert fake_redis.ttl("live_quote:NONE") == -2


def test_empty_tickers_makes_no_calls():
    fake = FakeYFinanceClient({})
    assert get_live_quotes([], fake) == {}
    assert fake.live_calls == []


@pytest.mark.parametrize(
    "cached",
    [
        '{"price": 1.0}',  # missing fields
        '{"price": 1.0, "prev_close": 1.0, "currency": "USD", "market_time": "bad", "trading_date": "2026-09-25"}',
        *(
            '{"price": %s, "prev_close": 1.0, "currency": "USD", '
            '"market_time": "2026-09-25T18:30:00+00:00", "trading_date": "2026-09-25"}' % bad
            for bad in ('"1.0"', "0", "-1", "NaN", "true")
        ),
        '{"price": 1.0, "prev_close": 0, "currency": "USD", '
        '"market_time": "2026-09-25T18:30:00+00:00", "trading_date": "2026-09-25"}',
    ],
)
def test_malformed_cache_entry_is_refetched(cached, fake_redis):
    fake_redis.setex("live_quote:AAPL", 300, cached)
    fake = FakeYFinanceClient({}, live_quotes={"AAPL": AAPL})

    assert get_live_quotes(["AAPL"], fake) == {"AAPL": AAPL}
    assert fake.live_calls == ["AAPL"]
    assert get_live_quotes(["AAPL"], fake) == {"AAPL": AAPL}
    assert fake.live_calls == ["AAPL"]  # re-cached with a valid entry
