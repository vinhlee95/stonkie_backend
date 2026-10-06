---
name: smoke-test
description: Boot the dev server from the current code and curl the endpoints a change affects, recording machine-generated evidence in .claude/smoke-state/. REQUIRED before finishing any work that touches runtime code (api/, services/, connectors/, models/, main.py, ...) — a Stop/SubagentStop hook blocks the agent until a passing run (or a justified waiver) exists for the current code.
when_to_use:
  - After changing an API endpoint, or a service/connector/model an endpoint uses
  - When the require-smoke-test hook blocks you from finishing
  - When an orchestrating agent needs to verify a subagent's "done" claim
  - When requested by user with /smoke-test
---

# Smoke Test

Unit tests prove the pieces; this proves the running app still answers. Every check goes through
`smoke.sh`, which runs the real request and writes the result itself — never hand-write or edit
files in `.claude/smoke-state/`.

```bash
S=.claude/skills/smoke-test/smoke.sh
```

## 1. Find what to hit

```bash
$S status
```

Prints the changed runtime files, the **fingerprint** of that code, routes that likely reach the
change (import-graph heuristic — confirm by reading the router), and anything already recorded.
Choose checks that actually execute the changed code path:

- every changed/added route: the happy path, plus the main error path you touched (404, 401, 422)
- a changed service/connector: each endpoint that calls it (at least one per router)
- auth-protected routes (`Depends(get_current_user)`): use `--auth`; also one call without it → 401

## 2. Boot the server from the current code

```bash
$S start
```

Starts `hypercorn main:app` on `127.0.0.1:${SMOKE_PORT:-8765}` (venv python), waits for
`/api/healthcheck`, logs to `.claude/smoke-state/server.log`. It always restarts, so the server
runs exactly the code being fingerprinted. If startup fails, the log tail is printed: that is a
real bug (import error, bad config) — fix it.

## 3. Run the checks

```bash
$S check GET  /api/tickers/search?q=AAPL --contains AAPL
$S check GET  /api/me/portfolio --auth
$S check GET  /api/me/portfolio --expect 401
$S check POST /api/me/portfolio/holdings/AAPL/lots --auth --expect 201 \
  -d '{"shares": 1, "price": 100, "purchased_on": "2026-01-02"}'
$S check POST /api/companies/AAPL/analyze --stream --max-time 30 \
  -d '{"question": "What is the revenue?"}' --contains '"type"'
```

Options: `--expect CODE` (default 200), `--contains TEXT`, `-d JSON`, `-H 'Header: v'`, `--auth`
(JWT for a smoke-test user via `BACKEND_JWT_SECRET`; the user is upserted in the dev DB),
`--stream` (SSE: a `--max-time` cut-off after data arrived counts as success), `--name LABEL`.

A `FAIL` prints the response and server log tail. Fix the code, then `start` again (any edit to
runtime code changes the fingerprint, and `check` refuses to run against a stale server).
Never change `--expect` just to turn a failure green — expectations come from the intended
behavior, not from what the server currently returns.

## 4. Stop

```bash
$S stop
```

## When it genuinely can't run

If the change can't be exercised over HTTP in this environment (e.g. the endpoint needs a
third-party API key that isn't configured, or a Celery-only task), record why — it is shown to
the user and to the orchestrating agent:

```bash
$S skip "Brave API key not configured in this env; covered by tests/services/test_x.py"
```

Missing local setup you can fix (no venv, server import error, DB not migrated) is not a reason
to skip. Mention any waiver explicitly in your final reply.

## Where results go

- `.claude/smoke-state/<fingerprint>.json` — one file per code state: changed files, every
  check (request, expected vs actual status, timing, response excerpt, server log tail on
  failure), and any waiver
- `.claude/smoke-state/latest.json` — copy of the most recently updated run

## Verifying a subagent's claim (orchestrator)

When a subagent reports runtime work as done, don't take the summary on trust:

1. `$S status` — the verdict line must be `pass` (or `waived` with an acceptable reason) for the
   **current** fingerprint; results for an older fingerprint don't count.
2. Read `.claude/smoke-state/<fingerprint>.json` and check that the checks cover the routes the
   change affects and that the response excerpts look right (not just status codes).
3. If coverage is thin, run the missing checks yourself.
