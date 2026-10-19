import pytest
import redis

from connectors import cache
from tests.api.test_quotes_price_changes import FakeRedis


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("connectors.cache.counter_client", fake)
    return fake


def test_first_increment_creates_the_counter_with_its_ttl(fake):
    assert cache.incr_with_ttl("k", 60) == 1
    assert fake.ttl("k") == 60


def test_later_increments_count_up_without_resetting_the_ttl(fake):
    cache.incr_with_ttl("k", 60)
    fake.expire("k", 12)  # time passes
    assert [cache.incr_with_ttl("k", 60) for _ in range(2)] == [2, 3]
    assert fake.ttl("k") == 12


def test_set_and_incr_go_out_in_one_transaction(monkeypatch):
    sent = []

    class Recorder(FakeRedis):
        def pipeline(self, transaction=True):
            sent.append(("pipeline", transaction))
            return super().pipeline(transaction)

        def set(self, key, value, ex=None, nx=False):
            sent.append(("set", key, ex, nx))
            return super().set(key, value, ex=ex, nx=nx)

    monkeypatch.setattr("connectors.cache.counter_client", Recorder())
    cache.incr_with_ttl("k", 60)
    assert sent == [("pipeline", True), ("set", "k", 60, True)]


def test_redis_timeout_fails_open(monkeypatch):
    class Hung(FakeRedis):
        def incr(self, key):
            raise redis.TimeoutError("timed out")

    monkeypatch.setattr("connectors.cache.counter_client", Hung())
    assert cache.incr_with_ttl("k", 60) is None
