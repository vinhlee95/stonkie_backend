# Phase 3 — Grounded Recap Generation & Trust Validator (TDD plan)

> Note: per project convention this plan should live at
> `backend/.claude/plans/weekly-us-market-recap-phase-3-tdd.md`. Plan-mode
> restricts edits to this single file, so move it after approval.

## Context

Phases 1–2 of the weekly US market recap pipeline are complete:
- Phase 1 established the data contract (`RecapPayload`, `MarketRecap` ORM, canonical URL/source_id utilities).
- Phase 2 produced a `RetrievalResult` of ranked, deduped candidates with `raw_content`, `published_date`, allowlist classification, and stable `source_id`s.

Phase 3 wires the **trust-critical core**: take `RetrievalResult` → produce a `RecapPayload` via a grounded LLM call → reject any output that would damage citation trust before persistence. No DB writes, no orchestration retries, no API surface yet (those are Phase 4/5).

The non-negotiable constraints from the PRD:
- Model consumes only the corpus we hand it (offline mode; no `:online`).
- Model references sources by **integer indices** into the provided corpus, never free-form URLs. Backend resolves indices → canonical `Source`s with stable `source_id`.
- Hard-fail rules **skip persistence entirely** (Phase 4 enforces the skip; Phase 3 just produces the verdict).
- Style/length/tone are prompt-only, not hard fails.

## Decisions (locked with user)

| Topic | Decision |
| --- | --- |
| Citation format from model | `source_indices` (ints into corpus) — `Citation.source_id` populated by backend mapping |
| Per-bullet min citations | ≥1 (already enforced by `Bullet.citations: Field(min_length=1)`) |
| Per-bullet allowlist rule | Hard fail if any bullet has 0 allowlisted citations |
| Recap-wide unique-source floor | **Soft**: log error/warning when `<3` distinct sources cited; do not hard-fail |
| Cited-source date window | Lenient: each cited `Source.published_at` must be in `[period_start − 1d, period_end + 1d]` |
| Stream handling | Concatenate `str` chunks, drop `dict` chunks, parse JSON inside `[RECAP_JSON]…[/RECAP_JSON]` markers |
| Model | Default `MultiAgent()` (no override); record concrete model string for `MarketRecap.model` audit |

## Public interfaces

### `services/market_recap/recap_generator.py`

```python
@dataclass(frozen=True)
class GeneratorResult:
    payload: RecapPayload      # source_id-shaped, ready for validator
    model: str                 # concrete model string used (for audit)
    raw_model_output: str      # full concatenated text (debug only)

class GeneratorError(Exception): ...           # malformed JSON, bad markers, bad indices

def generate_recap(
    retrieval: RetrievalResult,
    *,
    period_start: date,
    period_end: date,
    agent: MultiAgent | None = None,           # injectable for tests
) -> GeneratorResult
```

Behaviour:
1. Build a deterministic prompt: header (period, instructions, JSON contract), then `Source [i]` blocks (i, title, url, published_date, raw_content).
2. Call `agent.generate_content(prompt, use_google_search=False)` (offline). Iterate, keep only `isinstance(chunk, str)` (per CLAUDE.md gotcha).
3. Locate `[RECAP_JSON]…[/RECAP_JSON]`; `json.loads` the inner block.
4. Validate model JSON shape (`summary: str`, `bullets: [{text, source_indices: [int,…]}]`).
5. Map each `source_indices[k]` → `retrieval.candidates[k]` → `Source(...)`. Out-of-range → `GeneratorError`.
6. Build `RecapPayload(period_start, period_end, summary, bullets, sources)` (deduped sources by `source_id`). Pydantic re-validates citation→source coherence by construction.

### `services/market_recap/validator.py`

```python
@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    failures: list[str]    # hard-fail reason codes; ok == (failures == [])
    warnings: list[str]    # soft signals (e.g. unique-source floor)

REASON_OUT_OF_WINDOW = "cited_source_out_of_window"
REASON_BULLET_NO_ALLOWLISTED = "bullet_missing_allowlisted_source"

def validate_recap(
    payload: RecapPayload,
    *,
    period_start: date,
    period_end: date,
    grace_days: int = 1,
    min_unique_sources: int = 3,
) -> ValidationResult
```

Hard rules:
- Every cited `Source.published_at.date()` ∈ `[period_start − grace_days, period_end + grace_days]`.
- Every `Bullet` has ≥1 citation whose resolved `Source.url` is `is_allowlisted(...)`.

Soft rules (warnings only):
- `len({c.source_id for b in bullets for c in b.citations}) < min_unique_sources`.

Schema rules (already enforced by `RecapPayload`, restated by passthrough tests, not duplicated logic):
- ≥1 citation per bullet.
- Every citation references an existing `Source.id`.

## Files

Create:
- `backend/services/market_recap/recap_generator.py`
- `backend/services/market_recap/validator.py`
- `backend/tests/services/market_recap/test_recap_generator.py`
- `backend/tests/services/market_recap/test_validator.py`

Reuse (do not re-implement):
- `services/market_recap/schemas.py` — `RecapPayload`, `Bullet`, `Citation`, `Source`, `Candidate`, `RetrievalResult`.
- `services/market_recap/source_policy.py::is_allowlisted`.
- `services/market_recap/url_utils.py::source_id_for, canonicalize_url`.
- `agent/multi_agent.py::MultiAgent` (offline, default model).
- Existing test conftest (no DB needed for either file under test).

## TDD order of work — vertical slices

Each row is one **red→green→commit-eligible** slice. Do not write the next test until the previous is green.

### Generator slices

| # | Test name (intent) | Minimal impl to make it pass |
| --- | --- | --- |
| G1 | `test_prompt_contains_period_and_indexed_corpus` | Build prompt string from retrieval + dates; expose internal `_build_prompt` only via the call. Test asserts substrings on the captured `agent.generate_content` arg via a `FakeAgent`. |
| G2 | `test_extracts_recap_json_between_markers` | `FakeAgent` yields `"preamble [RECAP_JSON]{…}[/RECAP_JSON] trailing"`. Generator returns parsed payload with the right summary. |
| G3 | `test_resolves_source_indices_to_canonical_sources` | Two-candidate retrieval; model JSON cites indices `[0]` and `[1]`. Resulting `payload.sources[*].id` equals `source_id_for(candidate.url)` and bullets carry matching `source_id`. |
| G4 | `test_dedupes_sources_when_indices_repeat_across_bullets` | Two bullets both cite index `0`. `payload.sources` length is 1; both bullet citations resolve to the same `source_id`. |
| G5 | `test_filters_dict_chunks_from_stream` | `FakeAgent` yields a `url_citation` dict mid-stream; generator ignores it (no crash, no leak into JSON parse). |
| G6 | `test_missing_markers_raises_generator_error` | Output without `[RECAP_JSON]` fences → `GeneratorError`. |
| G7 | `test_out_of_range_source_index_raises_generator_error` | Index `[7]` against 2-candidate corpus → `GeneratorError`. |
| G8 | `test_returns_model_name_for_audit` | `GeneratorResult.model` equals the agent's reported model string (capture from a `FakeAgent` that exposes `.model_name`). |

`FakeAgent`: a tiny stub class providing `generate_content(prompt, use_google_search) -> Iterable[str|dict]`. No real network. Live in `tests/services/market_recap/test_recap_generator.py`.

### Validator slices

| # | Test name | Minimal impl |
| --- | --- | --- |
| V1 | `test_well_formed_payload_passes` | Two bullets, each cites a Reuters source (allowlisted) dated within window → `ok=True, failures=[]`. |
| V2 | `test_out_of_window_cited_source_fails` | One Source dated 5 days before `period_start` → `failures` contains `REASON_OUT_OF_WINDOW`. |
| V3 | `test_grace_day_within_one_day_passes` | Source dated `period_end + 1 day` → passes; `period_end + 2 days` → fails. (Two assertions in one test or split into V3a/V3b.) |
| V4 | `test_bullet_with_no_allowlisted_source_fails` | Bullet cites only a non-allowlisted domain (e.g. `random-blog.example`) → `failures` contains `REASON_BULLET_MISSING_ALLOWLISTED`. |
| V5 | `test_bullet_with_mixed_sources_passes_when_at_least_one_allowlisted` | Citations: one Reuters + one non-allowlisted → passes (corroboration is fine). |
| V6 | `test_unique_source_floor_warning` | All bullets cite the same single source → `ok=True`, `warnings` non-empty (soft rule); separate test asserts 3+ unique sources produces no warning. |
| V7 | `test_failures_accumulate_not_short_circuit` | Payload violating both window and allowlist rules surfaces both reason codes (operator diagnosability). |

Schema-level rules are not re-tested in the validator file; they are already covered by `tests/services/market_recap/test_schemas.py` from Phase 1.

### Refactor pass (after V7 green)

- Extract a tiny `_in_window(d, start, end, grace)` helper if duplicated.
- Confirm `validator.py` imports nothing from `recap_generator.py` (and vice versa) — they should be independently testable modules.

## Verification (PRD gates)

Run from `backend/` with venv:

```
source venv/bin/activate && PYTHONPATH=. pytest tests/services/market_recap/test_recap_generator.py -v
source venv/bin/activate && PYTHONPATH=. pytest tests/services/market_recap/test_validator.py -v
source venv/bin/activate && pytest tests/test_healthcheck.py -v
source venv/bin/activate && ruff check .
```

Evidence to capture for the PRD `validation_summary` update:
- Pytest output for both new files (Gates A & B).
- Negative test outputs covering: free-form URL ignored (G7 covers no-URL path; model can only emit indices), unknown index → fail, out-of-window → fail, missing allowlisted source per bullet → fail.
- Healthcheck + ruff green (Gate C).

After all green, update `.claude/plans/weekly-us-market-recap-prd.json` Phase 3 entry with `state: complete`, `validation_summary`, `learnings`, `gates_result_snapshot`, `next_phase_considerations` (Phase 4 will own retries, persistence, idempotency).

## Out of scope (Phase 4+)

- LLM call retries on `GeneratorError` / hard validation failure.
- DB persistence (`ON CONFLICT DO NOTHING`).
- CLI runner / Celery task / scheduler.
- Public API endpoint.

## Unresolved questions

- None blocking. Concrete `MultiAgent` default model name will be captured at runtime into `GeneratorResult.model`; if the user later wants to pin it, swap the default at the call site without touching tests.
