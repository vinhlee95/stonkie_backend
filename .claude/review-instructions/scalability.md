# Code review: Scalability/Performance

This checklist covers the scalability/performance angle of code review for the Stonkie backend (FastAPI served by hypercorn, PostgreSQL, Celery workers with Playwright, Cloud Run cronjobs, LLM + search API calls).

When reviewing a pull request, check:
- N+1: DB or API calls inside loops that could be batched (bulk select/upsert, batch yfinance download, parallel search).
- Blocking I/O in async code: sync HTTP/DB/yfinance calls inside `async def` without `run_in_executor`/`asyncio.to_thread`; `time.sleep` in async paths.
- Unbounded work: queries without LIMIT/pagination, loading whole tables, unbounded concurrency (`gather` over user-controlled lists), unbounded in-memory caches.
- Missing or broken caching for expensive/repeated calls (LLM, search, market data); cache keys that never hit.
- LLM cost/latency: extra sequential model calls that could be parallel or removed; oversized prompts.
- Celery: workers use `worker_max_tasks_per_child=1` for Playwright memory cleanup — flag changes to it or tasks that hold large objects/browsers without cleanup.
- DB: missing indexes for new query patterns (check new migrations), long transactions, sessions held across network calls.
- Streaming endpoints: buffering whole responses instead of streaming.

Out of scope for this checklist (covered by the other review instructions): logic bugs, layering, security, test coverage.
