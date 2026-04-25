# Weekly Market Recap — Operations Guide

Operator-facing reference for the `services.market_recap` pipeline (orchestrator + CLI in `scripts/run_market_recap.py`).

## Structured log events

Emitted by `services.market_recap.orchestrator` via stdlib `logging`. Each record carries `extra={"event": <name>, "fields": {...}}`. In Cloud Logging the `extra` dict surfaces as structured `jsonPayload` fields.

### `recap.run.start`

Fired once at the top of every `run_market_recap` call.

| Field | Type | Meaning |
|---|---|---|
| `run_id` | str (32-char hex) | Unique per run; correlates start with outcome. |
| `market` | str | e.g. `"US"`. |
| `cadence` | str | e.g. `"weekly"`. |
| `period_start` | str (ISO date) | Inclusive Monday of the recap week. |
| `period_end` | str (ISO date) | Inclusive Friday of the recap week. |

### `recap.run.outcome`

Fired exactly once per run, immediately before the orchestrator returns.

All `recap.run.start` fields plus:

| Field | Type | Meaning |
|---|---|---|
| `status` | str | One of `inserted`, `replaced`, `skipped_existing`, `validation_failed`, `generation_failed`. |
| `queries_total` | int | Planned queries issued to Tavily. |
| `results_total` | int | Total candidate results returned across all queries. |
| `fetched_ok` | int | Candidates with non-empty Tavily `extracted_content` (eligible for grounding). |
| `date_in_window_count` | int | Candidates inside the period window. Equals `results_total` because Tavily `start_date`/`end_date` is the authoritative phase-2 filter (see `weekly-us-market-recap-prd.json`). Will diverge only if a future phase adds post-Tavily date re-validation. |
| `allowlisted_count` | int | Candidates whose registrable domain is in the allowlist. |
| `cited_count` | int | Unique sources in the recap payload (`len(payload.sources)`). `0` on generation failure. |
| `validation_fail_reason` | str \| null | `;`-joined validator failure codes when `status == "validation_failed"`; `null` otherwise. |
| `inserted` | bool | True iff a row was written to `market_recap`. |

## Status values

- `inserted` — new row written for `(market, cadence, period_start)`.
- `replaced` — existing row replaced (only when CLI `--replace` flag is used).
- `skipped_existing` — row already existed; `ON CONFLICT DO NOTHING` no-op.
- `validation_failed` — all retry attempts produced payloads that failed hard validator rules; **no insert**.
- `generation_failed` — generator raised `GeneratorError` on every attempt; **no insert**.

## Diagnosing a run

Find a single run by its id:

```
jsonPayload.fields.run_id="abc123..."
```

Find all failed runs in a period:

```
jsonPayload.event="recap.run.outcome"
jsonPayload.fields.inserted=false
jsonPayload.fields.period_start>="2026-04-20"
```

Common log/DB consistency check: if `inserted=true` then `SELECT COUNT(*) FROM market_recap WHERE market=$1 AND cadence=$2 AND period_start=$3` returns 1.

## Rerun and rollback

CLI: `scripts/run_market_recap.py` (see `--help`).

- Rerun current week: default invocation with no flags.
- Rerun explicit period: `--period-start YYYY-MM-DD --period-end YYYY-MM-DD`.
- Replace an existing row (rare, exact period required): `--replace --period-start ... --period-end ...`.
- Backfill a bounded range: `--backfill --period-start ... --period-end ...` (CLI rejects spans beyond the configured maximum).

To roll back a single bad recap, delete the row and rerun:

```sql
DELETE FROM market_recap
 WHERE market = 'US' AND cadence = 'weekly' AND period_start = 'YYYY-MM-DD';
```

Then rerun the CLI for that period. Default-mode reruns are idempotent and safe.
