# Phase 6 — Stock Handlers V2 (strict TDD)

## Summary

Port the remaining **stock** question handlers to the v2 Brave-grounded pattern established in phase-5 `CompanyGeneralHandlerV2`. Three handlers in one bundled PR, strict red→green TDD per handler. ETF is dropped from phase-6 and tracked under a new phase-6.5 (added to PRD). v2's only job is swapping the source of truth for search results from `:online` → Brave-grounded passages; v1 infra (data optimizer, classifier, context builders, prompt components) is reused as-is.

## Design decisions (locked)

| # | Decision |
|---|----------|
| PR shape | Single bundled PR for the whole phase 6 |
| Scope | Stock only: `GeneralFinanceHandlerV2`, `CompanySpecificFinanceHandlerV2`, `CompanyComparisonHandlerV2`. ETF dropped → phase-6.5 |
| v1 reuse | Reuse v1 infra freely (read-only): `FinancialDataOptimizer`, `QuestionClassifier`, `context_builders/`, `PromptComponents`, `format_conversation_context`. Do not mutate v1 handler files. |
| Comparison concurrency | `asyncio.Semaphore(5)`; all tickers run, max 5 concurrent Brave calls |
| Partial failure | Prompt marker (`Note: web retrieval failed for $TSLA`) + `thinking_status` event listing failed tickers |
| Comparison thinking_status | Single aggregated status: e.g. `"Reading 12 sources across AAPL, MSFT, GOOG: Reuters, Bloomberg, FT, CNBC"`; deduped trusted publishers across all successful tickers |
| Source ID numbering (comparison) | Flat `[1]..[K]` across the combined ordered list; FE/`citation_index` unchanged |
| `raw_content` trim | None — full Brave passages stuffed; revisit only if eval shows pain (PRD Q6.3 closed) |
| CompanySpecificFinance search-decision | `use_google_search=True` → DB context **plus** Brave passages. `no_search` → DB context only, **preserve v1 thinking_status events** ("Figuring out…", "Loading…", "Analyzing…") |
| v1 fallback parity | Port both fallback branches (missing/undefined ticker w/ conv, no DB data w/ conv) **and** `attachment_url` event verbatim |
| Test granularity | Full mirrored 5-test shape per handler + comparison-specific tests + fallback tests |
| `FinancialAnalyzerV2` dispatch | Route the 4 stock `QuestionType` values; ETF dispatch deferred to phase-7 endpoint wiring |
| v1 regression gate | `pytest tests/services/ tests/test_healthcheck.py` |
| Order of work | GeneralFinance → CompanySpecificFinance → Comparison → dispatch wiring last |

## Files

### New
- `services/question_analyzer/handlers_v2.py` (extend with `GeneralFinanceHandlerV2`, `CompanySpecificFinanceHandlerV2`)
- `services/question_analyzer/comparison_handler_v2.py` (`CompanyComparisonHandlerV2`)
- `tests/services/question_analyzer/test_general_finance_handler_v2.py`
- `tests/services/question_analyzer/test_company_specific_finance_handler_v2.py`
- `tests/services/question_analyzer/test_comparison_handler_v2.py`

### Edited
- `services/financial_analyzer_v2.py` (extend dispatch to 4 stock `QuestionType` values; remove phase-5 "Unsupported in phase-5" fallthrough)

### Untouched
- v1 handlers (`handlers.py`, `comparison_handler.py`, `company_specific_finance_handler.py`)
- `services/etf_*` (frozen for phase-6; phase-6.5 covers ETF v2)
- `connectors/brave_client.py`, `services/analyze_retrieval/*` (already locked from earlier phases)

## Implementation steps (strict red→green per handler)

### Step 1 — `GeneralFinanceHandlerV2` (smallest)

1.1 **Red**: write `tests/services/question_analyzer/test_general_finance_handler_v2.py` with 5 tests mirroring phase-5:
- `test_no_search_emits_v1_like_sequence` — only "Writing your answer..." thinking_status, raw answer chunks, model_used, related_questions; no sources event
- `test_no_search_never_emits_sources_event`
- `test_search_on_emits_trusted_publishers_and_final_sources_once` — single combined `Reading N sources: <publishers>` thinking_status; final sources event with full metadata; raw `[N]` markers preserved in answer chunks
- `test_search_on_dedupes_trusted_publishers_in_single_status`
- `test_search_on_with_no_citations_emits_empty_sources_list`

Mock `MultiAgent`, `retrieve_for_analyze`. No live API. Assert ImportError red.

1.2 **Green**: implement `GeneralFinanceHandlerV2.handle(question, search_decision, use_url_context, preferred_model, conversation_messages, request_id)`:
- thinking_status "Writing your answer..." (matches v1 phase/step)
- format conversation_context via last 4 messages (mirror v1 `GeneralFinanceHandler` slice logic)
- if `search_decision.use_google_search`: resolve_market(country=None, question), call `retrieve_for_analyze`, build trusted-publisher thinking_status (deduped, untrusted omitted), build `Sources:\n[N] <title>\n<url>` block stuffed at prompt tail
- prompt mirrors v1 `GeneralFinanceHandler` body + adds `[N]` citation instruction when sources present
- stream `MultiAgent.generate_content(prompt, use_google_search=False)` raw text chunks (with `[N]` preserved)
- after stream end + retrieval ran: `build_sources_event(full_text, retrieved_sources)` exactly once
- yield `model_used`, then `_generate_related_questions`

1.3 Run `pytest tests/services/question_analyzer/test_general_finance_handler_v2.py -v` → green.

### Step 2 — `CompanySpecificFinanceHandlerV2`

2.1 **Red**: write `tests/services/question_analyzer/test_company_specific_finance_handler_v2.py` with:
- 5 mirrored phase-5 tests (no-search v1-like sequence preserving classify→data-fetch→analyze status events; no-search no-sources; search-on publishers + final sources once; dedupe; empty-citations)
- `test_attachment_url_emitted_for_single_quarter` (single quarterly statement → `attachment_url` event with 10-Q URL)
- `test_attachment_url_emitted_for_single_year` (single annual statement → `attachment_url` event with 10-K URL)
- `test_fallback_undefined_ticker_with_conversation` (ticker in {undefined,null,none,""}, conv messages present → conversation-context fallback path; no data fetch; no Brave; answer streams)
- `test_fallback_no_db_data_with_conversation` (data_requirement != NONE, no fundamental + no annual + no quarterly, conv present → fallback path)
- `test_search_on_stuffs_brave_after_db_context` (DB context built first, Brave `Sources:` block appended; both reach prompt)

Mock `MultiAgent`, `retrieve_for_analyze`, `FinancialDataOptimizer`, `QuestionClassifier`, `CompanyConnector`. Red on missing class.

2.2 **Green**: implement `CompanySpecificFinanceHandlerV2.handle(...)`:
- Reuse v1 `FinancialDataOptimizer`, `QuestionClassifier.classify_data_and_period_requirement`, `get_context_builder`, `PromptComponents` (analysis_focus, source_instructions, visual_output_instructions, build_filing_url_lookup) verbatim
- Port v1 fallback branches (missing ticker + conv, no-data + conv) verbatim — answer streams, no Brave, no sources event
- Port v1 `attachment_url` event for single-quarterly and single-annual
- Port v1 thinking_status sequence: "Figuring out what {TICKER} data you need...", "Loading {TICKER} {period_type} financial reports...", "Analyzing {TICKER} financials..."
- If `search_decision.use_google_search`: resolve_market from `company.country`, call `retrieve_for_analyze`, append aggregated trusted-publisher thinking_status, append `Sources:\n[N] ...` block to combined prompt (full `raw_content`, no trim)
- Stream `MultiAgent.generate_content(prompt, use_google_search=False)` — raw chunks, `[N]` preserved
- After stream end + retrieval ran: `build_sources_event(...)` exactly once
- model_used + related_questions

2.3 Green run.

### Step 3 — `CompanyComparisonHandlerV2`

3.1 **Red**: write `tests/services/question_analyzer/test_comparison_handler_v2.py`:
- `test_per_ticker_brave_fanout_with_semaphore_cap_5` — patch `retrieve_for_analyze` with an awaitable that records concurrent in-flight count; assert max in-flight ≤ 5 across N=8 tickers
- `test_aggregated_thinking_status_across_tickers` — single status, deduped publishers, ticker list included
- `test_partial_failure_proceeds_with_successes` — 1 of 4 tickers raises `BraveRetrievalError`; handler still produces an answer using 3 successful tickers' sources; failed-ticker `thinking_status` event present; prompt context includes failure marker (mock prompt capture)
- `test_all_failures_raises_brave_retrieval_error` — all tickers fail → `BraveRetrievalError` propagated (or error event yielded — match phase-5 contract)
- `test_flat_source_id_numbering_across_tickers` — 3 tickers × 2 sources each → final sources event has `[1]..[6]`, ordered by ticker order
- `test_no_search_path_runs_v1_like_comparison` (no Brave, mirrors v1 `CompanyComparisonHandler` short-form)
- `test_search_on_with_no_citations_emits_empty_sources_list`
- `test_dedupes_trusted_publishers_across_tickers`

Mock `retrieve_for_analyze`, `MultiAgent`, `CompanyConnector`, `CompanyFinancialConnector`. Red on missing class.

3.2 **Green**: implement `CompanyComparisonHandlerV2`:
- Reuse v1 `_fetch_companies_parallel`, `ComparisonCompanyBuilder`, `PromptComponents` verbatim
- thinking_status "Loading financial data for {tickers}..." then "Comparing {data_origin_parts}" (v1 parity)
- If `search_decision.use_google_search` (or v1's auto-enable for `google_search` data_source tickers):
  - `sem = asyncio.Semaphore(5)`
  - per-ticker coro: `async with sem: retrieve_for_analyze(question, market, request_id, brave_client, ticker)`; catch `BraveRetrievalError` → mark ticker failed
  - `asyncio.gather(*coros, return_exceptions=False)` (exceptions caught inside coros)
  - if all failed → raise `BraveRetrievalError` (or yield error event mirroring phase-5)
  - successful retrievals → flat-concatenate sources in ticker order, assign `[1]..[K]`
  - aggregated trusted-publisher thinking_status across all successes (deduped publishers + included ticker list)
  - if any failures → `thinking_status` event listing failed tickers; prompt context includes `Note: web retrieval failed for {failed_tickers}` marker
  - append `Sources:\n[N] ...` block to comparison prompt
- Stream `MultiAgent.generate_content(prompt, use_google_search=False)` — raw `[N]` preserved
- After stream end + any retrieval ran: `build_sources_event(...)` once
- v1 data_sources event still emitted (provenance) — preserved alongside new v2 sources event
- model_used + comparison-specific related_questions

3.3 Green run.

### Step 4 — Wire dispatch in `FinancialAnalyzerV2`

4.1 **Red**: extend `tests/services/question_analyzer/` (or a new `tests/services/test_financial_analyzer_v2_dispatch.py`) with:
- `test_dispatch_general_finance` — classifier returns GENERAL_FINANCE → `GeneralFinanceHandlerV2.handle` invoked once
- `test_dispatch_company_specific_finance`
- `test_dispatch_comparison` — classifier returns COMPANY_COMPARISON with tickers → `CompanyComparisonHandlerV2.handle` invoked
- `test_dispatch_company_general` (regression — phase-5 path still works)
- `test_unknown_classification_returns_error_answer`

4.2 **Green**: extend `FinancialAnalyzerV2.__init__` with handler instances, replace phase-5 `Unsupported question type in v2 phase-5` with full dispatch by `QuestionType`. Pass `request_id` into each handler.

4.3 Green run.

### Step 5 — Update PRD

5.1 Mark `phase-6-handler-v2-remaining` `state: complete` with validation_summary, tdd_evidence, gates_result_snapshot, learnings, next_phase_considerations.
5.2 Insert `phase-6.5-etf-handlers-v2` (state: not-started) into `phases[]` with sort_order between phase-6 and phase-7. Scope: ETFAnalyzerV2 + 4 ETF handlers v2 (general, overview, detailed, comparison). Update phase-7 dependencies to require `phase-6.5-etf-handlers-v2`.
5.3 Close PRD `global_unresolved_questions` Q6.3 (raw_content trim) — answered: drop trim, no measurable pain.

## Testing strategy

### Per-handler red→green
Each step's red run must show `ModuleNotFoundError` or `AttributeError` for the missing class, then green after implementation.

### Phase gates (PRD-mandated)
- `source venv/bin/activate && PYTHONPATH=. pytest tests/services/question_analyzer/ -v` — all v2 + v1 question_analyzer tests green
- `source venv/bin/activate && pytest tests/services/ tests/test_healthcheck.py -v` — v1 regression scope
- `source venv/bin/activate && ruff check .` — lint clean

### TDD evidence to capture in PRD validation_summary
- Red log per handler (initial test run before implementation)
- Green log per handler (after impl)
- Sample event sequence captured from at least 1 unit test per handler
- v1 regression run output

## Unresolved questions (concise)

- Q14.1 (PRD): per-ticker queries vs single combined for comparison — locked here as **per-ticker fanout** (PRD scope_includes already implies this); confirm at PR review
- Q6.4 (PRD): retain `:online` as hidden ops flag — out of scope; default `no` stands
- Whether the v1 comparison `data_sources` provenance event should be merged into the new sources event or kept separate — currently planned as **kept separate** (different semantics: provenance vs citations); FE renderer can ignore the legacy event
- All-tickers-fail behavior in comparison: raise `BraveRetrievalError` vs yield error event — to align with phase-5 contract; pick at impl time, default raise
