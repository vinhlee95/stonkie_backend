"""ChatLimitService: the only module in this package that does I/O (Redis counters via connectors.cache)."""

import asyncio
import time
from collections.abc import Callable, Mapping

from connectors import cache
from services.chat_limit.limits import WINDOWS, ChatLimitExceeded, client_ip, over_limit, window_key


class ChatLimitService:
    def __init__(
        self,
        incr: Callable[[str, int], int | None] | None = None,
        now: Callable[[], float] | None = None,
    ):
        self._incr = incr or cache.incr_with_ttl
        self._now = now or time.time

    async def check(self, headers: Mapping[str, str], peer: str | None) -> None:
        """Counts one chat for the caller; raises ChatLimitExceeded when a window is over its limit."""
        ip = client_ip({k.lower(): v for k, v in headers.items()}, peer)
        await asyncio.to_thread(self._check, ip)

    def _check(self, ip: str) -> None:
        now = self._now()
        # Stop at the first exceeded window, so a request rejected per-minute doesn't use up the daily quota.
        for window in WINDOWS:
            if over_limit(window, self._incr(window_key(window, ip, now), window.seconds)):
                raise ChatLimitExceeded(window)
