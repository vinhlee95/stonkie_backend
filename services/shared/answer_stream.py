"""Non-blocking answer streaming shared by the stock and ETF v2 analyze handlers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing
from typing import TypeVar

from utils.async_helpers import iterate_in_thread

T = TypeVar("T")

# Answer streams hold a worker for their whole length; their own pool keeps a burst of answers from
# starving the default executor that retrieval, DB and related-questions to_thread calls share.
ANSWER_LLM_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="analyze-answer-llm")


def answer_stream(stream: Iterable[T]) -> aclosing[AsyncIterator[T]]:
    """Pull a blocking LLM stream (e.g. ``_iter_answer_chunks(...)``) in ANSWER_LLM_POOL, one item per step,
    so the event loop serves other requests while the answer streams.

    Use as ``async with answer_stream(...) as chunks``: leaving the block (client disconnect, error) closes
    the LLM stream right away, and a cancelled consumer stops pulling after the item in flight.
    """
    return aclosing(iterate_in_thread(stream, ANSWER_LLM_POOL))
