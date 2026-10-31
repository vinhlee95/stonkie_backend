"""Fake LLM streams for the v2 answer-streaming tests. No LLM calls."""

from __future__ import annotations

import asyncio
import threading
from typing import Any


class GatedStream:
    """Fake MultiAgent.generate_content() result whose first chunk blocks until the event loop opens a gate.

    The gate is opened by `open_gate_when_waiting`, a task on the event loop. If the handler iterated this
    stream on the loop thread, the wait would block the loop, the task could never run and the wait would
    time out: `opened_in_time` tells the two apart.
    """

    def __init__(self, items: list[Any], timeout: float = 2.0):
        self.items = items
        self.timeout = timeout
        self.waiting = threading.Event()
        self.gate = threading.Event()
        self.opened_in_time: bool | None = None

    def __iter__(self):
        self.waiting.set()
        self.opened_in_time = self.gate.wait(timeout=self.timeout)
        yield from self.items


async def open_gate_when_waiting(stream: GatedStream) -> None:
    while not stream.waiting.is_set():
        await asyncio.sleep(0.005)
    stream.gate.set()
