from datetime import UTC, datetime

import pytest
import redis

from connectors import cache
from services.portfolio import PortfolioService, chat, rate_limit
from services.portfolio import service as portfolio_service
from tests.api.test_quotes_price_changes import FakeRedis

NOW = datetime.fromtimestamp(1_800_000_000, UTC)


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("connectors.cache.redis_client", fake)
    monkeypatch.setattr(portfolio_service, "_utcnow", lambda: NOW)
    monkeypatch.setattr(chat, "RATE_LIMIT_PER_MINUTE", 2)
    return fake


def allow(user_id: str) -> bool:
    return PortfolioService(portfolio=object(), yf_client=object(), fx=object(), companies=object())._allow_chat(
        user_id
    )


def test_window_key_is_per_scope_user_and_window():
    assert rate_limit.window_key("chat", "u1", 1_800_000_000.0, 60) == "rate:chat:u1:30000000"
    assert rate_limit.window_key("chat", "u1", 1_800_000_059.9, 60) == "rate:chat:u1:30000000"
    assert rate_limit.window_key("chat", "u1", 1_800_000_060.0, 60) == "rate:chat:u1:30000001"


def test_within_limit_fails_open_without_a_count():
    assert [rate_limit.within_limit(n, 2) for n in (1, 2, 3)] == [True, True, False]
    assert rate_limit.within_limit(None, 2) is True


def test_allows_up_to_the_limit_per_user(fake):
    assert [allow("u1") for _ in range(3)] == [True, True, False]
    assert allow("u2") is True


def test_counter_ttl_set_once_to_the_window(fake):
    allow("u1")
    allow("u1")

    (key,) = fake.store
    assert key.startswith("rate:portfolio_chat:u1:")
    assert fake.ttl(key) == 60
    assert fake.store[key][0] == "2"


def test_new_window_resets_the_count(fake, monkeypatch):
    monkeypatch.setattr(chat, "RATE_LIMIT_PER_MINUTE", 1)
    assert [allow("u1") for _ in range(2)] == [True, False]
    monkeypatch.setattr(portfolio_service, "_utcnow", lambda: datetime.fromtimestamp(1_800_000_060, UTC))
    assert allow("u1") is True


def test_fails_open_when_redis_is_down(monkeypatch):
    class Down:
        def incr(self, key):
            raise redis.ConnectionError("down")

    monkeypatch.setattr("connectors.cache.redis_client", Down())
    monkeypatch.setattr(chat, "RATE_LIMIT_PER_MINUTE", 1)

    assert cache.incr_with_ttl("k", 60) is None
    assert allow("u1") is True
