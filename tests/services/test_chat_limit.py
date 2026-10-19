import pytest

from services.chat_limit import ChatLimitExceeded, ChatLimitService
from services.chat_limit.limits import WINDOWS, client_ip

MINUTE, DAY = WINDOWS


def _counter():
    counts: dict[str, int] = {}

    def incr(key: str, _ttl: int) -> int:
        counts[key] = counts.get(key, 0) + 1
        return counts[key]

    return incr, counts


def test_client_ip_uses_last_forwarded_hop_then_peer():
    assert client_ip({"x-forwarded-for": "6.6.6.6, 2.2.2.2"}, "9.9.9.9") == "2.2.2.2"
    assert client_ip({"x-real-ip": "1.1.1.1"}, "9.9.9.9") == "9.9.9.9"
    assert client_ip({"x-forwarded-for": ""}, "9.9.9.9") == "9.9.9.9"
    assert client_ip({}, None) == "unknown"


def test_client_ip_rejects_non_ip_values():
    assert client_ip({"x-forwarded-for": "1.1.1.1, " + "a" * 500}, "9.9.9.9") == "9.9.9.9"
    assert client_ip({}, "testclient") == "unknown"


@pytest.mark.asyncio
async def test_allows_up_to_the_minute_limit_then_raises():
    incr, _ = _counter()
    service = ChatLimitService(incr=incr, now=lambda: 1_000_000.0)
    for _ in range(MINUTE.limit):
        await service.check({}, "1.1.1.1")
    with pytest.raises(ChatLimitExceeded) as exc:
        await service.check({}, "1.1.1.1")
    assert (exc.value.message, exc.value.retry_after) == (MINUTE.message, MINUTE.seconds)


@pytest.mark.asyncio
async def test_minute_rejections_do_not_use_up_the_daily_quota():
    incr, counts = _counter()
    service = ChatLimitService(incr=incr, now=lambda: 1_000_000.0)
    for _ in range(MINUTE.limit * 3):
        try:
            await service.check({}, "1.1.1.1")
        except ChatLimitExceeded:
            pass
    day_counts = [count for key, count in counts.items() if key.startswith("rate:chat:day:")]
    assert day_counts == [MINUTE.limit]


@pytest.mark.asyncio
async def test_ips_are_counted_separately():
    incr, _ = _counter()
    service = ChatLimitService(incr=incr, now=lambda: 1_000_000.0)
    for _ in range(MINUTE.limit):
        await service.check({}, "1.1.1.1")
    await service.check({}, "2.2.2.2")


@pytest.mark.asyncio
async def test_new_minute_resets_minute_window_but_day_window_still_caps():
    incr, _ = _counter()
    clock = {"now": 0.0}
    service = ChatLimitService(incr=incr, now=lambda: clock["now"])
    for i in range(DAY.limit):
        clock["now"] = (i // MINUTE.limit) * MINUTE.seconds
        await service.check({}, "1.1.1.1")
    clock["now"] += MINUTE.seconds
    with pytest.raises(ChatLimitExceeded) as exc:
        await service.check({}, "1.1.1.1")
    assert (exc.value.message, exc.value.retry_after) == (DAY.message, DAY.seconds)


@pytest.mark.asyncio
async def test_counter_store_down_fails_open():
    service = ChatLimitService(incr=lambda _key, _ttl: None)
    for _ in range(MINUTE.limit * 3):
        await service.check({}, "1.1.1.1")
