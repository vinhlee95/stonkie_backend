# Phase 0 — Chat-specific source policy (TDD)

## Context

Analyze v2 migrates from OpenRouter `:online` to a Brave-backed retrieval pipeline (per `backend/.claude/plans/analyze-v2-brave-migration-prd.json`). The pipeline needs a chat-specific allowlist+tiering policy that is **independent** of the existing `services/market_recap/source_policy.py` (recap is out of scope, owns its own list).

Phase 0 is the foundation: a pure-function policy module + tests. No I/O, no Brave calls, no orchestration. Later phases (goggle building, ranking) consume it.

## Approach

Mirror the structure of `services/market_recap/source_policy.py` (file:1-95), but:
- Two-tier per market (TIER_1 boost=4, TIER_2 boost=2) instead of flat allowlist
- Markets: `GLOBAL` (default), `VN`, `FI`. FI = GLOBAL ∪ FI_EXTENSION. VN does **not** inherit GLOBAL.
- Wildcard `*.gov` → any `.gov` subdomain is GLOBAL TIER_1
- Hard `DISCARDS` list with **precedence over allowlists**
- `Market` typed as `Literal["GLOBAL", "VN", "FI"]`

Duplicate (not import) `registrable_domain` from recap. PRD locks "no imports across recap and analyze_retrieval".

## Files to create

- `backend/services/analyze_retrieval/__init__.py` (empty package marker)
- `backend/services/analyze_retrieval/source_policy.py` — module under test
- `backend/tests/services/analyze_retrieval/__init__.py`
- `backend/tests/services/analyze_retrieval/test_source_policy.py` — TDD test file
- `backend/tests/services/analyze_retrieval/fixtures/brave_csf_01.json` (copy of `tmp/analyze_eval/full01/csf-01/brave.json`)
- `backend/tests/services/analyze_retrieval/fixtures/brave_cg_01.json` (copy of `cg-01/brave.json`)
- `backend/tests/services/analyze_retrieval/fixtures/brave_news_01.json` (copy of `news-01/brave.json`)
- `backend/tests/services/analyze_retrieval/fixtures/brave_etf_01.json` (copy of `etf-01/brave.json`)
- `backend/tests/services/analyze_retrieval/fixtures/brave_vn_01.json` (copy of `vn-01/brave.json`)

## Module API (`source_policy.py`)

```python
Market = Literal["GLOBAL", "VN", "FI"]

GLOBAL_TIER_1: frozenset[str]      # per PRD locked_policy.GLOBAL.TIER_1
GLOBAL_TIER_2: frozenset[str]
GLOBAL_TIER_1_WILDCARDS: tuple[str, ...] = (".gov",)  # *.gov → tier 1 (GLOBAL/FI only)
FI_EXTENSION_TIER_1: frozenset[str]
FI_EXTENSION_TIER_2: frozenset[str]
VN_TIER_1: frozenset[str]
VN_TIER_2: frozenset[str]
DISCARDS: frozenset[str]            # plain domains
DISCARD_PATH_PREFIXES: tuple[tuple[str, str], ...] = (("tradingview.com", "/ideas"),)

def registrable_domain(url: str) -> str: ...   # duplicated, VN multi-suffix aware
def is_discarded(url: str) -> bool: ...
def tier_for(url: str, market: Market) -> int | None: ...
def is_trusted(url: str, market: Market) -> bool: ...   # tier_for is not None
```

Resolution rules in `tier_for`:
1. If `is_discarded(url)` → return `None` (discards always win).
2. Compute domain via `registrable_domain`.
3. If market is `VN` → check `VN_TIER_1` then `VN_TIER_2`. Return early; **no GLOBAL fallback**, **no `*.gov` wildcard**.
4. Else (`GLOBAL` or `FI`) → tier 1 = `GLOBAL_TIER_1` ∪ (`FI_EXTENSION_TIER_1` if market FI) ∪ `*.gov` wildcard match; tier 2 = `GLOBAL_TIER_2` ∪ (`FI_EXTENSION_TIER_2` if market FI).
5. Return tier int or `None`.

## TDD plan (red → green)

### Step 1: write failing tests first

`tests/services/analyze_retrieval/test_source_policy.py` covers:

**Tier resolution (GLOBAL):**
- `tier_for("https://reuters.com/article/x", "GLOBAL") == 1`
- `tier_for("https://www.reuters.com/x", "GLOBAL") == 1`
- `tier_for("https://markets.ft.com/x", "GLOBAL") == 1`
- `tier_for("https://investing.com/x", "GLOBAL") == 2`
- `tier_for("https://random-blog.com/x", "GLOBAL") is None`
- `tier_for("https://sec.gov/x", "GLOBAL") == 1`
- `tier_for("https://anything.gov/x", "GLOBAL") == 1` (wildcard `*.gov`)
- `tier_for("https://sub.dept.gov/x", "GLOBAL") == 1`
- `tier_for("https://companieshouse.gov.uk/x", "GLOBAL") == 1` (explicit, NOT via *.gov)

**Tier resolution (VN — isolated):**
- `tier_for("https://cafef.vn/x", "VN") == 1`
- `tier_for("https://www.hsx.vn/x", "VN") == 1`
- `tier_for("https://ssi.com.vn/x", "VN") == 2`
- `tier_for("https://reuters.com/x", "VN") is None`
- `tier_for("https://random-blog.vn/x", "VN") is None`

**Tier resolution (FI — union by tier number):**
- `tier_for("https://inderes.fi/x", "FI") == 1`
- `tier_for("https://kauppalehti.fi/x", "FI") == 1`
- `tier_for("https://hs.fi/x", "FI") == 2`
- `tier_for("https://reuters.com/x", "FI") == 1`
- `tier_for("https://investing.com/x", "FI") == 2`

**Discards (precedence):**
- `is_discarded("https://reddit.com/r/x") is True`
- `is_discarded("https://medium.com/x") is True`
- `is_discarded("https://www.tradingview.com/ideas/abc") is True` (path-prefix)
- `is_discarded("https://www.tradingview.com/symbols/abc") is False`
- Edge: monkeypatch DISCARDS to include `reuters.com` → `tier_for("https://reuters.com/x", "GLOBAL") is None` (discards win over tier-1).

**`is_trusted` mirror:**
- True for any tier-1 / tier-2 hit; False for discarded or unknown.

**Tier non-overlap invariant:**
- For each market, `TIER_1 ∩ TIER_2 == ∅`.

**Snapshot test (exact counts — Gate evidence):**
- Resolved tier-1/tier-2 sets per market match: GLOBAL T1=13, T2=16; VN T1=7, T2=10; FI adds T1=2, T2=3.

**Independence guard:**
- AST parse `services/analyze_retrieval/source_policy.py` and assert no `import` references `services.market_recap`.

Run: `source venv/bin/activate && PYTHONPATH=. pytest tests/services/analyze_retrieval/test_source_policy.py -v` → **red**. Capture log.

### Step 2: implement to green

- Create `services/analyze_retrieval/__init__.py` and `source_policy.py` with locked policy data + helpers.
- Duplicate `registrable_domain` from `services/market_recap/source_policy.py:72-88` (VN multi-suffix aware). No `import` from recap.
- `is_discarded`: lowercase hostname; check `registrable_domain in DISCARDS`; check path-prefix table.
- `tier_for`: discards-first short-circuit, market dispatch as above.
- `is_trusted = tier_for(...) is not None`.

Run pytest → **green**. Capture log.

### Step 3: copy fixture files

`cp backend/tmp/analyze_eval/full01/{csf-01,cg-01,news-01,etf-01,vn-01}/brave.json` → `backend/tests/services/analyze_retrieval/fixtures/brave_<name>_01.json`. Phase 0 does not consume them; they are committed for phase 1+.

### Step 4: baseline gates (mandatory per PRD)

- `source venv/bin/activate && pytest tests/test_healthcheck.py -v`
- `source venv/bin/activate && ruff check .`

### Step 5: PRD update + stop

Update phase-0 in `backend/.claude/plans/analyze-v2-brave-migration-prd.json`: `state: complete`, `validation_summary`, `learnings`, `gates_result_snapshot`, `tdd_evidence` (red+green logs), `next_phase_considerations`. Stop. Wait for explicit user approval before phase 1 (manual_approval_required).

## Order of work

1. Create empty `services/analyze_retrieval/__init__.py` and `tests/services/analyze_retrieval/__init__.py` (so test discovery works) — but DO NOT create `source_policy.py` yet.
2. Write `tests/services/analyze_retrieval/test_source_policy.py` with all tests above.
3. Run pytest → confirm RED (ImportError). Capture log.
4. Implement `services/analyze_retrieval/source_policy.py`.
5. Run pytest → confirm GREEN. Capture log.
6. Copy 5 brave.json fixtures into `tests/services/analyze_retrieval/fixtures/`.
7. Run baseline: `pytest tests/test_healthcheck.py` + `ruff check .`. Both must pass.
8. Update PRD JSON phase-0 block with evidence.
9. Stop and request user approval before phase 1.

## Verification (end-to-end)

1. Red: pytest fails with ImportError.
2. Green: pytest passes after implementation.
3. Healthcheck + ruff pass.
4. Fixture files committed.
5. PRD JSON updated.

## Reused references

- `services/market_recap/source_policy.py:72-88` — `registrable_domain` to duplicate (forbidden to import).
- `tests/services/market_recap/test_source_policy.py` — test style to mirror.

## Resolved decisions

- `tradingview.com/ideas` → path-prefix block only.
- FI stacking → union by tier number.
- Snapshot test → exact hardcoded counts.
- `*.gov` wildcard → GLOBAL/FI only; VN excluded.
