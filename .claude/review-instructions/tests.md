# Code review: Tests

This checklist covers the tests angle of code review for the Stonkie backend (pytest, run as `python -m pytest`; tests under `tests/` mirroring `api/`, `services/`, `connectors/`).

When reviewing a pull request, check:
- New or changed behavior in `api/`, `services/`, `connectors/`, `tasks/` without a corresponding test change. Severity `high` for new core logic with no test.
- **Tests must never call external APIs** (OpenRouter, Gemini, Brave, Tavily, Alpha Vantage, yfinance network, Google OAuth). Any test doing real network I/O is `high`.
- Services must be tested with fake connectors injected via constructor/param, not by patching SessionLocal or hitting a real DB unless the test is explicitly a connector test using the test DB fixtures in `conftest.py`.
- Weak tests: no assertions, asserting only "no exception", asserting on mocks instead of behavior, over-mocking the unit under test.
- Missing edge-case tests for the edge cases the change itself handles (empty input, NaN, unknown ticker, auth failure).
- Brittle tests: depending on current date/time without freezing, ordering of dict/set, sleeps.
- New fixtures in `conftest.py` that leak state between tests.

Out of scope for this checklist (covered by the other review instructions): production-code logic bugs, layering, security, performance.
