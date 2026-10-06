import contextlib
import contextvars
from concurrent.futures import ThreadPoolExecutor

import pytest

from utils.async_helpers import iterate_in_thread

request_id = contextvars.ContextVar("request_id", default=None)


async def collect(iterable, executor=None):
    return [item async for item in iterate_in_thread(iterable, executor)]


@pytest.mark.asyncio
async def test_yields_items_in_order_and_handles_empty():
    assert await collect(iter([1, 2, 3])) == [1, 2, 3]
    assert await collect([]) == []


@pytest.mark.asyncio
async def test_iterator_errors_propagate():
    def broken():
        yield 1
        raise RuntimeError("stream died")

    with pytest.raises(RuntimeError, match="stream died"):
        await collect(broken())


@pytest.mark.asyncio
async def test_context_is_visible_inside_the_iterator_on_every_step():
    def seen():
        for _ in range(3):
            yield request_id.get()

    request_id.set("req-1")
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert await collect(seen(), pool) == ["req-1"] * 3


@pytest.mark.asyncio
async def test_breaking_early_leaves_the_pool_usable():
    with ThreadPoolExecutor(max_workers=1) as pool:
        async for _ in iterate_in_thread(iter(range(100)), pool):
            break
        assert await collect(iter("ab"), pool) == ["a", "b"]


@pytest.mark.asyncio
async def test_breaking_early_closes_the_underlying_generator():
    closed = []

    def stream():
        try:
            yield from range(100)
        finally:
            closed.append(True)

    async with contextlib.aclosing(iterate_in_thread(stream())) as items:
        async for _ in items:
            break

    assert closed == [True]
