# Phase 5 — CompanyGeneral v2 + Analyzer v2 Wiring (Strict TDD)

## Summary
Implement Phase 5 with strict TDD constraints and no endpoint wiring: add `CompanyGeneralHandlerV2` and wire it into `FinancialAnalyzerV2`. Preserve v1 behavioral shape where requested, while introducing v2 retrieval + inline citation flow (`[N]` preserved in streamed answer, final `sources` event emitted once).

This phase establishes the first complete v2 service path (analyzer -> handler -> retrieval -> stream) so later phases can port remaining handlers and then wire the v2 route.

## Design Decisions
- Keep handler interface v1-style, but remove `use_google_search` flag.
- Pass `SearchDecision` object into v2 handler/analyzer flow.
- Retrieval is owned by `CompanyGeneralHandlerV2` (not analyzer).
- `no_search` path uses v1-like event sequence: `thinking_status` -> `answer` chunks -> `model_used` -> `related_question` events; no `sources` event.
- Search-on path emits one combined `thinking_status` listing publishers from sources fed to LLM, filtered to trusted publishers only, no cap.
- Prompt is near-parity with v1 company-general prompt, with explicit inline `[N]` citation contract.
- TDD process uses small batched vertical slices (2-3 related tests per slice), still red->green per slice.

## Scope
- In scope:
  - `services/question_analyzer/handlers_v2.py` (new `CompanyGeneralHandlerV2`)
  - `services/financial_analyzer_v2.py` (wiring for company-general path only; no endpoint)
  - `tests/services/question_analyzer/test_company_general_handler_v2.py`
  - `tests/services/test_financial_analyzer_v2.py` (or equivalent analyzer v2 test path)
- Out of scope:
  - API route wiring (`api/analyze_v2.py`, `main.py` include router)
  - Other v2 handlers (general finance / specific finance / comparison / ETF)
  - Frontend changes

## Implementation Steps
1. **Create test scaffolding (slice 1: no_search baseline)**
   - Add handler v2 test module.
   - Write failing tests for no-search v1-like event sequence:
     - emits `thinking_status` first
     - streams `answer` chunks
     - emits `model_used`
     - emits related questions
     - emits no `sources`
   - Mock LLM stream and related-question generator via public handler behavior.

2. **Implement minimal handler v2 for no_search (slice 1 green)**
   - Add `CompanyGeneralHandlerV2` in `handlers_v2.py`.
   - Implement v1-parity prompt baseline + conversation context handling.
   - Accept `search_decision` input; branch no-search path first.
   - Keep output event schema compatible with existing SSE serializer dict format.

3. **Add retrieval-on tests (slice 2 red)**
   - Failing tests for search-on flow:
     - handler calls retrieval once
     - emits exactly one combined `thinking_status` publisher message (trusted-only, deduped, from sources fed to LLM)
     - answer chunks preserve `[N]` markers verbatim
     - emits final single `sources` event from `build_sources_event(...)`
     - no intermediate source-json or paragraph events

4. **Implement retrieval-on behavior (slice 2 green)**
   - In handler, call `retrieve_for_analyze(...)` when `search_decision` requires retrieval.
   - Build stuffed prompt from retrieved sources (ordered source list defines `[N]` mapping).
   - Stream LLM output raw; accumulate text for final `build_sources_event(full_text, retrieved_sources)`.
   - Emit final `sources` exactly once after stream completion.

5. **Error-path tests and implementation (slice 3 red->green)**
   - Failing tests for retrieval failure:
     - if retrieval raises `BraveRetrievalError`, handler propagates phase-consistent error event behavior expected by analyzer (for later route mapping).
   - Implement minimal robust error behavior without endpoint assumptions.

6. **Analyzer v2 tests (slice 4 red)**
   - Add failing tests for `FinancialAnalyzerV2` company-general wiring:
     - classifier/search-decision dispatch reaches `CompanyGeneralHandlerV2`
     - passes `search_decision` through
     - preserves stream event ordering from handler
   - No route or cache assertions here.

7. **Implement analyzer v2 minimal wiring (slice 4 green)**
   - Create `services/financial_analyzer_v2.py`.
   - Mirror essential orchestration structure from v1 where needed.
   - Wire company-general classification path only (others can remain stubs/not implemented for this phase if tests define this boundary).

8. **Refactor pass**
   - Remove duplication in prompt/source assembly if exposed by slices.
   - Keep imports layer-safe for future phase-7 architecture gate.

9. **Phase verification**
   - Run targeted phase tests first, then baseline gates:
     - `source venv/bin/activate && PYTHONPATH=. pytest tests/services/question_analyzer/test_company_general_handler_v2.py -v`
     - `source venv/bin/activate && PYTHONPATH=. pytest tests/services/test_financial_analyzer_v2.py -v`
     - `source venv/bin/activate && pytest tests/test_healthcheck.py -v`
     - `source venv/bin/activate && ruff check .`

## Testing Strategy (Strict TDD)
- Use batched vertical slices (2-3 related behavior tests per slice), never implementation-first.
- Prefer behavior assertions through public async generator output sequence, not internals.
- Mock external boundaries only (LLM streaming + retrieval HTTP boundary).
- Keep fixture-driven deterministic outputs for citation mapping and publisher list derivation.
- Maintain explicit red evidence and green evidence per slice in phase notes.

## Unresolved Questions
- Should phase-5 include only company-general dispatch in `FinancialAnalyzerV2`, or include placeholder wiring for other classifications?
- PRD dependency label mismatch: `phase-4-paragraph-citation-postprocessor` vs completed `phase-4-citation-index-collector` (treat as same dependency?).

