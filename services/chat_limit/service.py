"""ChatLimitService: the only module in this package that does I/O (Redis counters via connectors.cache)."""

import asyncio
import time
from collections.abc import Callable

from connectors import cache
from services.chat_limit.limits import WINDOWS, ChatLimitExceeded, exceeded, window_key


class ChatLimitService:
    def __init__(
        self,
        incr: Callable[[str, int], int | None] | None = None,
        now: Callable[[], float] | None = None,
    ):
        self._incr = incr or cache.incr_with_ttl
        self._now = now or time.time

    async def check(self, ip: str) -> None:
        """Counts one chat for `ip`; raises ChatLimitExceeded when any window is over its limit."""
        await asyncio.to_thread(self._check, ip)

    def _check(self, ip: str) -> None:
        now = self._now()
        counts = [self._incr(window_key(w, ip, now), w.seconds) for w in WINDOWS]
        if window := exceeded(counts):
            raise ChatLimitExceeded(window)
