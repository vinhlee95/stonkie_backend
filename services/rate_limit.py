"""Fixed-window per-user rate limits backed by Redis. Fails open when Redis is down."""

import time

from connectors import cache


def allow(scope: str, user_id: str, limit: int, window_seconds: int) -> bool:
    window = int(time.time()) // window_seconds
    count = cache.incr_with_ttl(f"rate:{scope}:{user_id}:{window}", window_seconds)
    return count is None or count <= limit
