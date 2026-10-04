"""asyncio helpers for driving blocking code."""

import asyncio
import contextvars
from collections.abc import AsyncIterator, Iterable, Iterator
from concurrent.futures import Executor
from typing import TypeVar

T = TypeVar("T")
_DONE = object()


async def iterate_in_thread(iterable: Iterable[T], executor: Executor | None = None) -> AsyncIterator[T]:
    """Consume a blocking iterator (e.g. an LLM stream) without blocking the event loop."""
    loop = asyncio.get_running_loop()
    # Same context for every step, so tracing (langfuse) inside the iterator keeps its parent span.
    context = contextvars.copy_context()
    iterator: Iterator[T] = iter(iterable)
    while True:
        item = await loop.run_in_executor(executor, context.run, next, iterator, _DONE)
        if item is _DONE:
            return
        yield item
