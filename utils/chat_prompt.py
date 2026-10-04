"""Helpers shared by the streaming chat services."""

import asyncio
import re
from collections.abc import AsyncIterator, Iterable, Iterator
from typing import TypeVar

T = TypeVar("T")
_DONE = object()


def format_conversation(messages: list[dict[str, str]] | None, limit: int = 6) -> str:
    """The last `limit` messages as "ROLE: text" lines for a prompt, or "" when there are none."""
    lines = []
    for msg in (messages or [])[-limit:]:
        role = (msg.get("role") or "").upper()
        content = re.sub(r"\s+", " ", msg.get("content") or "").strip()
        if role and content:
            lines.append(f"{role}: {content}")
    return "Recent conversation:\n" + "\n".join(lines) if lines else ""


def extract_answer_text(chunks: list) -> str:
    """Langfuse transform: the answer text out of a list of streamed events."""
    return "".join(c.get("body", "") for c in chunks if isinstance(c, dict) and c.get("type") == "answer")


async def iterate_in_thread(iterable: Iterable[T]) -> AsyncIterator[T]:
    """Consume a blocking iterator (e.g. an LLM stream) without blocking the event loop."""
    iterator: Iterator[T] = iter(iterable)
    while True:
        item = await asyncio.to_thread(next, iterator, _DONE)
        if item is _DONE:
            return
        yield item
