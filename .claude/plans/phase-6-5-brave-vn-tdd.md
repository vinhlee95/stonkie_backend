# Phase 6.5 — Brave LLM-Context for VN + Daily Cadence (TDD plan)

## Context

Side-by-side test (PRD §rationale.evidence_summary) shows Tavily returns ~0 Vietnamese-language sources for VN queries; Brave LLM-Context with `country=ALL`, `search_lang=vi`, `count=30` plus an inline VN-allowlist Goggle reaches 71–86% allowlist hit rate. Phase 6.5 fully replaces Tavily for VN with Brave (no flag, no fallback), expands the VN allowlist from 11 → 30 entries, fixes `registrable_domain` for multi-part `.com.vn`/`.gov.vn`/`.org.vn`/`.net.vn`/`.edu.vn` suffixes, splits validator rules so VN downgrades per-bullet allowlist to a warning while hard-failing on a recap-wide `<2 distinct allowlisted sources` floor, and adds `cadence=daily` as a manually-runnable mode (Cloud Scheduler stays in Phase 7). US weekly path must remain byte-identical.

PRD: [`backend/.claude/plans/weekly-us-market-recap-prd.json`](weekly-us-market-recap-prd.json) §`phase-6-5-brave-vn-and-daily-cadence`.

## Order of work (TDD red → green → refactor)

Each gate is one cycle: write the listed tests **first**, watch them fail (red), implement the smallest change to pass (green), then refactor. Run that gate's pytest target after each cycle. Do **not** advance to the next gate until the prior gate's tests are green.

### Gate A — `registrable_domain` multi-suffix fix + VN allowlist expansion

Reason this gate runs first: every later gate depends on `is_allowlisted("…com.vn", market="VN")` returning the right answer. Cheapest, most isolated change.

- **Red** — extend [`tests/services/market_recap/test_source_policy.py`](../../tests/services/market_recap/test_source_policy.py):
  - parametrized `registrable_domain` cases:
    - `https://ssi.com.vn/x` → `ssi.com.vn`
    - `https://www.hsc.com.vn/x` → `hsc.com.vn`
    - `https://ssc.gov.vn/x` → `ssc.gov.vn`
    - `https://www.vir.com.vn/x` → `vir.com.vn`
    - `https://hsx.vn/x` → `hsx.vn` (2-label, regression)
    - `https://www.cafef.vn/x` → `cafef.vn` (subdomain regression)
    - `https://www.reuters.com/x` → `reuters.com` (US regression)
    - one each for `.org.vn`, `.net.vn`, `.edu.vn` synthetic.
  - `is_allowlisted` cases for every one of the 19 PRD additions under `market="VN"` → `True`.
  - Negative: `https://random-blog.vn/x` → `False`.
- **Green** — [`services/market_recap/source_policy.py`](../../services/market_recap/source_policy.py): add `_VN_MULTI_SUFFIXES = (".com.vn", ".gov.vn", ".org.vn", ".net.vn", ".edu.vn")`; in `registrable_domain` strip the matched suffix, take the trailing label, rejoin. Append the 19 domains from PRD `vn_allowlist_expansion.additions` to `ALLOWLIST_BY_MARKET["VN"]`.
- **Verify**: `pytest tests/services/market_recap/test_source_policy.py -v`.

### Gate B — `BraveClient` + Goggle builder

- **Red** — new [`tests/services/market_recap/test_brave_client.py`](../../tests/services/market_recap/test_brave_client.py) and a new fixture `tests/services/market_recap/fixtures/brave/llm_context_response.json` (model after `fixtures/tavily/search_response.json`; capture a real-shape response with `grounding.generic[].snippets[]` and a `sources` map containing `age` arrays — derive from artifact `scripts/brave_vs_tavily_vn.json`).
  - `BraveClient.search(...)` returns `list[Candidate]` with `provider="brave"`.
  - For each Brave hit:
    - `raw_content` = `"\n\n".join(grounding.generic[i].snippets)` verbatim.
    - `published_date` = first parseable element of `sources[url].age` (ISO `YYYY-MM-DD` typically index 1); else midpoint = `datetime(year, month, day, 12, 0, tzinfo=UTC)` of `(period_start + period_end) / 2`.
    - `score` defaults to `0.0` (Brave does not expose one).
  - Request shape assertions (mock `httpx`/`requests` client): URL `https://api.search.brave.com/res/v1/llm/context`, header `X-Subscription-Token: <key>`, query params `country=ALL`, `search_lang=vi`, `count=30`, `freshness=YYYY-MM-DDtoYYYY-MM-DD`, `goggles=<inline def>`.
  - Goggle builder: deterministic string built from `ALLOWLIST_BY_MARKET["VN"]`; given a fixed allowlist input, output is byte-stable (snapshot test).
  - `include_domains` arg from the Protocol is accepted and ignored (VN planner emits `[]`).
  - Empty-response edge: 0 Brave hits → `[]`, no exceptions.
  - Unparseable date list edge: midpoint fallback exercised.
- **Green** — new [`services/market_recap/brave_client.py`](../../services/market_recap/brave_client.py) implementing `SearchProvider`. Add a small `_build_vn_goggle()` helper (private; tested via snapshot). Reuse `Candidate` from `schemas.py`; reuse `_parse_iso_datetime`-style helper from `tavily_client.py` (refactor: extract to a shared `_dates.py` only if the duplication is non-trivial — otherwise inline).
- **Verify**: `pytest tests/services/market_recap/test_brave_client.py -v`.

### Gate C — Provider routing in `retrieval.py` + planner cleanup

- **Red** — extend [`tests/services/market_recap/test_retrieval.py`](../../tests/services/market_recap/test_retrieval.py) and [`test_query_planner.py`](../../tests/services/market_recap/test_query_planner.py):
  - `retrieve_candidates(market="US", ...)` selects `TavilyClient` (assert provider on returned candidates / mock dispatch).
  - `retrieve_candidates(market="VN", ...)` selects `BraveClient`.
  - `plan_queries(market="VN", cadence="weekly", ...)` returns exactly **one** `PlannedQuery(query="thị trường chứng khoán Việt Nam tuần qua", include_domains=[])`.
  - `plan_queries(market="VN", cadence="daily", ...)` returns one `PlannedQuery(query="thị trường chứng khoán Việt Nam phiên hôm nay", include_domains=[])`.
  - `plan_queries(market="US", ...)` is **byte-identical to today** (regression — keep existing assertions intact).
- **Green** —
  - [`services/market_recap/query_planner.py`](../../services/market_recap/query_planner.py): add `cadence` arg (default `"weekly"`); for `market="VN"` short-circuit to a single PlannedQuery from a `_VN_TEMPLATES` dict; US branch unchanged. Drop VN entry from `HIGH_SIGNAL_SITES_BY_MARKET` (no longer used).
  - [`services/market_recap/retrieval.py`](../../services/market_recap/retrieval.py): introduce `_provider_for(market)` returning the right `SearchProvider` instance; thread `cadence` through to `plan_queries`. Keep the existing `search_provider` injection point so tests can still pass a mock.
  - **Delete** the VN-Tavily code path (any `market == "VN"` branch that constructs Tavily). Grep evidence in DoD.
- **Verify**: `pytest tests/services/market_recap/test_retrieval.py tests/services/market_recap/test_query_planner.py -v`.

### Gate D — Validator rule split (VN per-bullet warning, recap-wide hard-fail)

- **Red** — extend [`tests/services/market_recap/test_validator.py`](../../tests/services/market_recap/test_validator.py):
  - VN payload: one bullet cites a non-allowlisted source, others cite allowlisted → `ok=True`, `warnings` contains `bullet_missing_allowlisted_source`, `failures` does not.
  - VN payload: only 1 distinct allowlisted source recap-wide → `ok=False`, `failures` contains a new constant e.g. `vn_recap_allowlist_floor_below_minimum`.
  - VN payload: 2+ distinct allowlisted sources recap-wide → no allowlist failure.
  - VN existing rules (vn-index, macro, money flow, out-of-window) remain unchanged — explicit regression cases.
  - US regression: identical inputs as a captured pre-Phase-6.5 case → identical `ValidationResult` (snapshot or field-by-field).
- **Green** — [`services/market_recap/validator.py`](../../services/market_recap/validator.py): branch on `market.upper() == "VN"` for the allowlist segment. For VN, demote the per-bullet check to a warning and add a recap-wide distinct-allowlisted-sources count with a hard-fail when `< 2`. New constant `REASON_VN_RECAP_ALLOWLIST_FLOOR`. Leave US logic literally untouched.
- **Verify**: `pytest tests/services/market_recap/test_validator.py -v`.

### Gate E — Orchestrator `cadence="daily"` + `provider` log field

- **Red** — extend [`tests/services/market_recap/test_orchestrator.py`](../../tests/services/market_recap/test_orchestrator.py) and [`test_observability.py`](../../tests/services/market_recap/test_observability.py):
  - `run_market_recap(market="VN", cadence="daily", period_start=d, period_end=d, ...)` succeeds end-to-end with mocked Brave fixture.
  - `run.start` and `run.outcome` log records carry `provider="brave"` for VN, `provider="tavily"` for US.
  - `run.outcome` carries `cadence="daily"` when invoked daily.
  - US weekly orchestrator regression case unchanged (`provider="tavily"`, `cadence="weekly"`).
- **Green** — [`services/market_recap/orchestrator.py`](../../services/market_recap/orchestrator.py): pass `cadence` through to `retrieve_fn` so it reaches `plan_queries`. Resolve `provider` from market (`"brave"` for VN, `"tavily"` for US) and add to `base_fields` before `EVENT_RUN_START`.
- **Verify**: `pytest tests/services/market_recap/test_orchestrator.py tests/services/market_recap/test_observability.py -v`.

### Gate F — CLI `--cadence daily` + VN tz default

- **Red** — extend [`tests/scripts/test_run_market_recap.py`](../../tests/scripts/test_run_market_recap.py):
  - `--market VN --cadence daily` with no `--period-start` defaults to the latest completed VN trading day in `Asia/Ho_Chi_Minh` (Mon–Fri only; on Sat → Friday, on Sun → Friday, on Mon → Friday).
  - `--market VN --cadence daily --period-start 2026-04-22` overrides; `period_start == period_end == 2026-04-22`.
  - `--market VN --cadence weekly --period-start 2026-04-20 --period-end 2026-04-24` still routes through the weekly path (regression).
  - `--market US` default unchanged (still uses NY tz, weekly Mon–Fri).
  - `--cadence daily --backfill-start ... --backfill-end ...` is rejected (parser error) — keep backfill weekly-only in v1.
- **Green** — [`scripts/run_market_recap.py`](../../scripts/run_market_recap.py): add `VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")` and `compute_latest_completed_trading_day(market)`; branch period defaulting on `args.cadence == "daily"`; reject `daily + backfill`. Pass `cadence` through unchanged (already does).
- **Verify**: `pytest tests/scripts/test_run_market_recap.py -v`.

### Gate G — Env, docs, full-suite green

- Add `BRAVE_API_KEY=` to [`backend/.env.example`](../../.env.example) (after `TAVILY_API_KEY`).
- Update [`backend/docs/market_recap_operations.md`](../../docs/market_recap_operations.md): Brave request shape, expanded VN allowlist + `registrable_domain` multi-suffix policy, daily cadence runbook, VN validator rule split (per-bullet warning + recap-wide hard-fail).
- **Verify** (DoD `required_commands`):
  ```
  source venv/bin/activate && PYTHONPATH=. pytest tests/services/market_recap/test_brave_client.py -v
  source venv/bin/activate && PYTHONPATH=. pytest tests/services/market_recap/test_source_policy.py -v
  source venv/bin/activate && PYTHONPATH=. pytest tests/services/market_recap/test_query_planner.py -v
  source venv/bin/activate && PYTHONPATH=. pytest tests/services/market_recap/test_retrieval.py -v
  source venv/bin/activate && PYTHONPATH=. pytest tests/services/market_recap/test_validator.py -v
  source venv/bin/activate && PYTHONPATH=. pytest tests/services/market_recap/test_orchestrator.py -v
  source venv/bin/activate && PYTHONPATH=. pytest tests/scripts/test_run_market_recap.py -v
  source venv/bin/activate && pytest tests/test_healthcheck.py -v
  source venv/bin/activate && ruff check .
  ```
- **Manual smoke** (not pytest):
  ```
  PYTHONPATH=. python scripts/run_market_recap.py --market VN --cadence weekly --period-start 2026-04-20 --period-end 2026-04-24
  PYTHONPATH=. python scripts/run_market_recap.py --market VN --cadence daily
  ```
  Confirm DB row inserted for (`VN`, `weekly`, `2026-04-20`) and (`VN`, `daily`, `<latest VN trading day>`); confirm `run.outcome` log carries `provider="brave"`.
- **Grep evidence** (DoD): `! rg "TavilyClient" services/market_recap/ | rg -i "vn"` returns no hits.

## Critical files

Modified:
- [`services/market_recap/source_policy.py`](../../services/market_recap/source_policy.py)
- [`services/market_recap/query_planner.py`](../../services/market_recap/query_planner.py)
- [`services/market_recap/retrieval.py`](../../services/market_recap/retrieval.py)
- [`services/market_recap/validator.py`](../../services/market_recap/validator.py)
- [`services/market_recap/orchestrator.py`](../../services/market_recap/orchestrator.py)
- [`scripts/run_market_recap.py`](../../scripts/run_market_recap.py)
- [`.env.example`](../../.env.example)
- [`docs/market_recap_operations.md`](../../docs/market_recap_operations.md)

New:
- `services/market_recap/brave_client.py`
- `tests/services/market_recap/test_brave_client.py`
- `tests/services/market_recap/fixtures/brave/llm_context_response.json`

Reused (no edits, but relied upon):
- `services/market_recap/schemas.py` (`Candidate`, `PlannedQuery`)
- `services/market_recap/search_client.py` (`SearchProvider` Protocol)
- `services/market_recap/logging.py` (`new_run_id`, `log_event`)
- `services/market_recap/tavily_client.py` (date-parsing pattern to mirror)
- Fixture pattern: `tests/services/market_recap/fixtures/tavily/search_response.json`

## Assumptions (worth confirming before coding)

1. Daily cadence applies to **VN only** in v1; `--market US --cadence daily` is out of scope (the PRD `cadence_extension` is silent on US daily; planner will not have a US daily template). Will reject at the CLI parser if `--cadence daily --market US`.
2. Existing VN content rules in `validator.py` (`vn_index_missing`, `vn_macro_context_missing`, `vn_money_flow_missing`) are **kept as hard-fails**; the rule split only touches the allowlist segment. PRD does not contradict.
3. Goggle definition format = the inline `! $boost=N, host=domain` syntax used by Brave Goggles; one boost line per VN allowlist entry, deterministic ordering (sorted). Snapshot-locked in tests.
4. `provider` log field is added to `run.start` **and** `run.outcome` (PRD calls out both).
5. No new `provider` column on the `Source` schema (explicit PRD scope_excludes).

## Unresolved questions

- Do you want `--market US --cadence daily` to also be supported, or strictly VN-only as assumed?
- Should the recap-wide allowlist floor constant be exported under a name you prefer (current pick: `REASON_VN_RECAP_ALLOWLIST_FLOOR`)?
- For the Goggle, OK to embed the allowlist literally in the URL query string (it's ~30 short host lines), or do you want it hosted externally (PRD says no external hosting in v1 — assuming inline)?
