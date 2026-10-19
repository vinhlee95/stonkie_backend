import pytest

from services.chat_limit import ChatLimitExceeded, ChatLimitService, client_ip
from services.chat_limit.limits import WINDOWS


def _counter():
    counts: dict[str, int] = {}

    def incr(key: str, _ttl: int) -> int:
        counts[key] = counts.get(key, 0) + 1
        return counts[key]

    return incr, counts


def test_client_ip_prefers_real_ip_then_first_forwarded_hop_then_peer():
    assert client_ip({"x-real-ip": "1.1.1.1", "x-forwarded-for": "2.2.2.2"}, "9.9.9.9") == "1.1.1.1"
    assert client_ip({"x-forwarded-for": "2.2.2.2, 10.0.0.1"}, "9.9.9.9") == "2.2.2.2"
    assert client_ip({}, "9.9.9.9") == "9.9.9.9"
    assert client_ip({}, None) == "unknown"


@pytest.mark.asyncio
async def test_allows_up_to_the_minute_limit_then_raises():
    incr, _ = _counter()
    service = ChatLimitService(incr=incr, now=lambda: 1_000_000.0)
    minute = WINDOWS[0]
    for _ in range(minute.limit):
        await service.check("1.1.1.1")
    with pytest.raises(ChatLimitExceeded) as exc:
        await service.check("1.1.1.1")
    assert exc.value.window is minute


@pytest.mark.asyncio
async def test_ips_are_counted_separately():
    incr, _ = _counter()
    service = ChatLimitService(incr=incr, now=lambda: 1_000_000.0)
    for _ in range(WINDOWS[0].limit):
        await service.check("1.1.1.1")
    await service.check("2.2.2.2")


@pytest.mark.asyncio
async def test_new_minute_resets_minute_window_but_day_window_still_caps():
    incr, _ = _counter()
    clock = {"now": 0.0}
    service = ChatLimitService(incr=incr, now=lambda: clock["now"])
    minute, day = WINDOWS
    for i in range(day.limit):
        clock["now"] = (i // minute.limit) * minute.seconds
        await service.check("1.1.1.1")
    clock["now"] += minute.seconds
    with pytest.raises(ChatLimitExceeded) as exc:
        await service.check("1.1.1.1")
    assert exc.value.window is day


@pytest.mark.asyncio
async def test_counter_store_down_fails_open():
    service = ChatLimitService(incr=lambda _key, _ttl: None)
    for _ in range(WINDOWS[0].limit * 3):
        await service.check("1.1.1.1")
