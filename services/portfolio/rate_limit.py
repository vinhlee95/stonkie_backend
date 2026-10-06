"""Fixed-window per-user rate limits. PortfolioService keeps the counters in Redis and fails open."""


def window_key(scope: str, user_id: str, now: float, window_seconds: int) -> str:
    return f"rate:{scope}:{user_id}:{int(now) // window_seconds}"


def within_limit(count: int | None, limit: int) -> bool:
    """`count` is None when the counter store is down: fail open."""
    return count is None or count <= limit
