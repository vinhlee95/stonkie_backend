import pytest
import redis

from connectors import cache
from services import rate_limit
from tests.api.test_quotes_price_changes import FakeRedis


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("connectors.cache.redis_client", fake)
    monkeypatch.setattr(rate_limit, "_now", lambda: 1_800_000_000.0)
    return fake


def test_allows_up_to_the_limit_per_user_and_scope(fake):
    assert [rate_limit.allow("chat", "u1", 2, 60) for _ in range(3)] == [True, True, False]
    assert rate_limit.allow("chat", "u2", 2, 60) is True
    assert rate_limit.allow("other", "u1", 2, 60) is True


def test_counter_ttl_set_once_to_the_window(fake):
    rate_limit.allow("chat", "u1", 5, 60)
    rate_limit.allow("chat", "u1", 5, 60)

    (key,) = fake.store
    assert fake.ttl(key) == 60
    assert fake.store[key][0] == "2"


def test_new_window_resets_the_count(fake, monkeypatch):
    assert [rate_limit.allow("chat", "u1", 1, 60) for _ in range(2)] == [True, False]
    monkeypatch.setattr(rate_limit, "_now", lambda: 1_800_000_060.0)
    assert rate_limit.allow("chat", "u1", 1, 60) is True


def test_fails_open_when_redis_is_down(monkeypatch):
    class Down:
        def incr(self, key):
            raise redis.ConnectionError("down")

    monkeypatch.setattr("connectors.cache.redis_client", Down())

    assert cache.incr_with_ttl("k", 60) is None
    assert rate_limit.allow("chat", "u1", 1, 60) is True
