# Weekly US Market Recap — Phase 2 TDD Plan

**PRD**: [.claude/plans/weekly-us-market-recap-prd.json](weekly-us-market-recap-prd.json) — phase `phase-2-retrieval-and-source-policy-layer`

## Context

Phase 1 (data contract + storage) is complete. Phase 2 builds the retrieval pipeline that feeds the LLM in Phase 3: Tavily search → dedupe → soft allowlist → ranking → top-K candidates, all behind a provider-agnostic interface so a fallback search provider can be swapped in later without touching ranking or policy code. No LLM, no persistence, no API surface in this phase.

This plan follows strict vertical-slice TDD: one test → minimal impl → next test. No bulk test writing.

## Locked decisions (from Q&A)

| Topic | Decision |
|---|---|
| Allowlist | Python constant in `source_policy.py`. List: reuters, apnews, bloomberg, wsj, ft, cnbc, marketwatch, barrons, nytimes, sec.gov, federalreserve.gov, bls.gov, bea.gov, treasury.gov, nyse, nasdaq. Match by registrable domain (`markets.ft.com` → `ft.com`). |
| Query planner | 1 open query: `"US stock market recap week of {Mon} {D}-{D}, {YYYY}"` + 6 site-scoped (one per high-signal site: reuters, apnews, cnbc, marketwatch, sec.gov, federalreserve.gov) using template `"US market week recap {Mon} {D}-{D} {YYYY}"`. Total: 7 Tavily calls/run. |
| Tavily params | `search_depth="basic"`, `topic="news"`, `max_results=5`, `include_raw_content=True`, `start_date`/`end_date` set. `include_domains=[site]` for site-scoped only. |
| Date filter | Trust Tavily's `start_date`/`end_date`. No belt-and-suspenders filter. |
| Ranking | Lexicographic: allowlisted first → freshness desc → Tavily `score` desc → canonical URL (stable tiebreak). No diversity cap. **top_k = 5**. |
| Dedupe | By `source_id` (Phase 1's `source_id_for`). On collision: keep higher Tavily score. |
| Fetcher fallback | **Deferred** — drop from Phase 2 scope. Tavily `raw_content` is sole content source; candidates with empty `raw_content` are dropped before ranking. |
| Coordinator | New file `retrieval.py` with `retrieve_candidates(period_start, period_end) -> RetrievalResult`. |
| Search interface | `Protocol` (`SearchProvider`) in `search_client.py`. `Candidate` model added to existing `schemas.py`. |
| Tests | Hand-crafted JSON fixtures under `tests/services/market_recap/fixtures/tavily/`. Fake `SearchProvider` for `retrieval.py` tests. One focused HTTP test for `TavilyClient` using `respx` or `httpx.MockTransport`. **No live calls.** |
| Stage counters | `RetrievalStats(queries_total, results_total, deduped, with_raw_content, allowlisted, ranked_top_k)`. |

## Files

### New
- `backend/services/market_recap/source_policy.py` — `ALLOWLIST` constant, `registrable_domain(url)`, `is_allowlisted(url)`
- `backend/services/market_recap/query_planner.py` — `plan_queries(period_start, period_end) -> list[PlannedQuery]`
- `backend/services/market_recap/search_client.py` — `SearchProvider` Protocol
- `backend/services/market_recap/tavily_client.py` — `TavilyClient` (httpx, implements `SearchProvider`)
- `backend/services/market_recap/ranking.py` — `dedupe(candidates)`, `rank(candidates) -> list[Candidate]`
- `backend/services/market_recap/retrieval.py` — `retrieve_candidates(...)`, `RetrievalResult`, `RetrievalStats`
- `backend/tests/services/market_recap/test_source_policy.py`
- `backend/tests/services/market_recap/test_query_planner.py`
- `backend/tests/services/market_recap/test_tavily_client.py`
- `backend/tests/services/market_recap/test_ranking.py`
- `backend/tests/services/market_recap/test_retrieval.py`
- `backend/tests/services/market_recap/fixtures/tavily/*.json`

### Modified
- `backend/services/market_recap/schemas.py` — add `Candidate`, `PlannedQuery`, `RetrievalStats`, `RetrievalResult` (reuse Phase 1 utilities; do not redefine source contract)
- `backend/.env.example` — add `TAVILY_API_KEY`, remove `GOOGLE_CSE_API_KEY` / `GOOGLE_CSE_ENGINE_ID` if present
- `backend/requirements.txt` — add `respx` (test dep) if not already pinned; no Tavily SDK (call HTTP directly via existing `httpx`)

## Reuse from Phase 1

- `services/market_recap/url_utils.py::canonicalize_url` — drives dedupe & `source_id`
- `services/market_recap/url_utils.py::source_id_for` — dedupe key
- `services/market_recap/schemas.py` — extend, do not duplicate

## Vertical TDD slices (execute in order)

Each slice = one RED test → minimal GREEN impl → commit-ready. Do **not** write the next test until the previous one is green.

1. **Allowlist match by registrable domain.**
   Test: `is_allowlisted("https://markets.ft.com/data")` → True; `"https://example.com"` → False; `"https://www.reuters.com/..."` → True.
   Impl: `ALLOWLIST` set + `registrable_domain()` (use `tldextract` if already in deps, else hand-rolled suffix match against the constant set).

2. **Query planner — 1 open + 6 site-scoped.**
   Test: `plan_queries(date(2026,4,20), date(2026,4,24))` returns 7 `PlannedQuery` objects: one with no `include_domains`, six with single-domain `include_domains` matching the high-signal site list. Verify the date-formatted query string for the open query.
   Impl: templated string formatter; site list as a module constant.

3. **TavilyClient HTTP normalization (single focused HTTP test).**
   Test: with `respx`/`MockTransport` returning a canned Tavily JSON fixture, `TavilyClient().search(query, ...)` returns `list[Candidate]` with `title, url, snippet, published_date, raw_content, score, provider="tavily"` populated; missing `raw_content` becomes empty string (not None drop here — drop happens later).
   Impl: minimal httpx POST to `https://api.tavily.com/search`, header `Authorization: Bearer {TAVILY_API_KEY}`, body shape per locked decisions; map response fields → `Candidate`.

4. **Dedupe by source_id keeps higher score.**
   Test: two candidates with URLs that canonicalize to the same value (one with `?utm_source=...`), different scores → dedupe returns one, with the higher score.
   Impl: group by `source_id_for(canonicalize_url(url))`, reduce by max score.

5. **Drop empty raw_content before ranking.**
   Test: input list with mixed `raw_content` populated/empty → only populated survive into ranking input; counter reflects this.
   Impl: filter step inside `retrieval.py`.

6. **Ranking is lexicographic and deterministic.**
   Test: fixture with mixed allowlisted/non-allowlisted, varied dates and scores → `rank(...)` produces the exact expected order. Re-run with shuffled input → identical output (determinism).
   Impl: `sorted(..., key=lambda c: (not is_allowlisted(c.url), -published_ts, -c.score, c.canonical_url))`.

7. **End-to-end `retrieve_candidates` with fake provider returns top-5 + stats.**
   Test: fake `SearchProvider` returns canned per-query fixtures (covering open + site-scoped). `retrieve_candidates(period)` returns `RetrievalResult(candidates=[≤5], stats=RetrievalStats(...))` with each counter matching expected values from the fixture set. Allowlisted candidates appear before non-allowlisted in `candidates`.
   Impl: wire planner → provider.search per planned query → flatten → dedupe → drop empty raw_content → compute `allowlisted` count → rank → top 5; populate stats; return.

After slice 7 is green, run `ruff check .` and `pytest tests/test_healthcheck.py -v` (mandatory baseline gates per PRD).

## Verification (matches PRD Phase 2 gates)

Commands to run from `backend/` with venv active:

```
PYTHONPATH=. pytest tests/services/market_recap/test_source_policy.py -v
PYTHONPATH=. pytest tests/services/market_recap/test_query_planner.py -v
PYTHONPATH=. pytest tests/services/market_recap/test_tavily_client.py -v
PYTHONPATH=. pytest tests/services/market_recap/test_ranking.py -v
PYTHONPATH=. pytest tests/services/market_recap/test_retrieval.py -v
pytest tests/test_healthcheck.py -v
ruff check .
```

Required evidence to record back into the PRD's `validation_summary` for `phase-2-retrieval-and-source-policy-layer`:

- Tavily fixture → normalized `Candidate` list (slice 3)
- Allowlisted vs non-allowlisted classification fixture (slice 1)
- `RetrievalStats` snapshot from slice 7 showing `queries_total → results_total → deduped → with_raw_content → allowlisted → ranked_top_k`
- Determinism proof: shuffled-input ranking returns identical order (slice 6)

Per PRD `agent_validation_protocol`: stop after Phase 2 completes and request user approval before starting Phase 3. Update PRD entry with `validation_summary`, `learnings`, `gates_result_snapshot`, `next_phase_considerations`.

## Out of scope (explicitly deferred)

- httpx + trafilatura fetcher fallback (separate plan)
- LLM generation, validator (Phase 3)
- Orchestrator, persistence, CLI runner (Phase 4)
- Public API endpoint (Phase 5)
- Observability hardening / structured logging schema (Phase 6)
