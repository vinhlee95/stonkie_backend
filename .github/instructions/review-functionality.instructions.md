---
applyTo: "**"
excludeAgent: "cloud-agent"
---

# Code review: Functionality

This checklist covers the functionality angle of code review for the Stonkie backend (FastAPI + PostgreSQL/SQLAlchemy + Celery + LLM calls via OpenRouter/Gemini, market data via yfinance).

When reviewing a pull request, check:
- Does the code do what the PR title, description and commit messages say it should? Missing cases, wrong conditions, off-by-one, inverted logic.
- Edge cases: empty/None inputs, empty lists, unknown tickers, missing DB rows, timezones and trading-day boundaries (weekends, holidays, date vs datetime).
- Error paths: exceptions swallowed or leaking as 500s; partial writes; retries that duplicate work.
- Regressions: changed function signatures/return shapes whose callers were not updated (search for callers).
- Async correctness: missing `await`, sync generators used as async, generator exhaustion.
- `MultiAgent.generate_content()` / `OpenRouterClient.stream_chat()` yield `Union[str, dict]`; code iterating over them must use `_process_source_tags()` or an `isinstance(chunk, str)` guard.
- yfinance data can contain NaN (e.g. latest daily Close); non-finite floats must be guarded before JSON serialization.
- API contract: response shape/status codes consistent with existing endpoints under `/api/companies/{ticker}/...`.

Out of scope for this checklist (covered by the other review instructions): layering/conventions, security, performance, test coverage.
