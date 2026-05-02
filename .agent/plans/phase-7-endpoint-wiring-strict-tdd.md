# Phase 7 Endpoint Wiring Strict TDD Plan

## Summary

Build the stock-only v2 analyze endpoint at `POST /api/v2/companies/{ticker}/analyze`, mounted through a new `api/analyze_v2.py` router. The endpoint should preserve the v1 conversation/cookie streaming contract, route stock requests through `FinancialAnalyzerV2`, isolate v2 semantic cache entries, translate Brave retrieval failures into SSE errors, and keep the route thin under the three-layer architecture rules.

This phase intentionally skips the new plain `meta` SSE event and `BRAVE_API_KEY` health/readiness work. Those are PRD deviations chosen during planning and must be recorded in the phase-7 PRD closeout.

## Design Decisions

- Public interface: `POST /api/v2/companies/{ticker}/analyze` accepts the same JSON body shape as v1: `question`, `useUrlContext`, `deepAnalysis`, `preferredModel`, and optional `conversationId`.
- SSE ordering: v2 starts with `conversation`, matching v1. Do not emit a new plain `{type: "meta"}` event in this phase.
- Request IDs: keep internal `request_id` values for retrieval/logging, but do not expose a route-level `meta` event.
- V1 parity default: preserve v1 route behavior unless this plan explicitly says otherwise. That includes anonymous-user cookie handling, `conversationId` reuse/generation, storage ticker normalization, previous conversation lookup, user/assistant message persistence, and passing `conversation_messages`, `conversation_id`, and `anon_user_id` into `FinancialAnalyzerV2.analyze_question`.
- ETF behavior before phase 7.5: ETF tickers return a streaming SSE error event with `type: "error"` and `code: "not_supported"`.
- Healthcheck: skip `BRAVE_API_KEY` health/readiness changes in this phase.
- Cache isolation: reuse the existing `semantic_cache` table with `v2:{TICKER}` cache keys and force v2 cache entries to expire after 30 minutes.
- Visual stream parity: stock v2 live streams and v2 cache replays must pass answer text through `VisualAnswerStreamSplitter`, matching v1 behavior for HTML/SVG visual blocks.
- Layering: `api/analyze_v2.py` stays Layer 1 only. It may import FastAPI, request/response types, `FinancialAnalyzerV2`, and service-level helpers, but must not import `connectors.*`, `models.*`, or handler modules directly.
- Closeout: after implementation passes, update `.claude/plans/analyze-v2-brave-migration-prd.json` with phase-7 `validation_summary`, `learnings`, `gates_result_snapshot`, and explicit notes for skipped `meta` and healthcheck items.

## TDD Strategy

Use strict vertical slices. Do not write all tests upfront. Each behavior below should be implemented as one red-green cycle:

1. Write one failing test for the behavior.
2. Implement the smallest code path that makes that test pass.
3. Run that focused test.
4. Repeat for the next behavior.
5. Refactor only while green.

Prefer integration-style endpoint tests through `TestClient` and SSE parsing. Mock only boundaries: analyzer stream, ETF lookup, conversation store, semantic cache connector, time/UUID if needed, and external retrieval failures.

## Implementation Steps

1. Tracer bullet: v2 route preserves v1 cookie/conversation behavior.
   - Add the first test in `tests/test_analyze_v2_endpoint.py`.
   - Exercise `POST /api/v2/companies/AAPL/analyze` through `TestClient`.
   - Mock `FinancialAnalyzerV2.analyze_question` to stream a tiny answer.
   - Assert response is `text/event-stream`, sets `anon_user_id` when absent, emits `conversation` first, and then emits analyzer events.
   - Implement `api/analyze_v2.py`, instantiate `FinancialAnalyzerV2`, and mount it from `main.py` via `app.include_router`.

2. Preserve request parsing and conversation hydration.
   - Add the next endpoint test with an existing `conversationId` and cookie.
   - Assert v1 session parity: existing `anon_user_id` cookie is reused, provided `conversationId` is reused, `storage_ticker` matches v1 normalization, and `get_conversation_history_for_prompt(anon_user_id, storage_ticker, conv_id)` is called before analyzer execution.
   - Assert `FinancialAnalyzerV2.analyze_question` receives the looked-up `conversation_messages`, the same `conversation_id`, and the same `anon_user_id`, so follow-up questions get prior conversation context just like v1.
   - Assert `append_user_message` runs before streaming analyzer output and `append_assistant_message` persists the accumulated assistant answer after the live stream, matching v1 behavior.
   - Keep this logic in the route or a small Layer-2 stream helper if the route starts getting too chunky.

3. ETF temporary behavior.
   - Add a test where `get_etf_by_ticker("SPY")` returns data.
   - Assert the v2 endpoint streams `{"type": "error", "code": "not_supported"}` and does not call `FinancialAnalyzerV2`.
   - Implement the stock-vs-ETF guard.

4. Brave retrieval failure translation.
   - Add a test where the v2 analyzer raises `BraveRetrievalError`.
   - Assert the stream contains `{"type": "error", "code": "retrieval_failed"}` and closes cleanly.
   - Implement the error translation at the v2 stream boundary.

5. v2 semantic cache namespace isolation.
   - Add a cache hit/miss test proving v2 uses `v2:AAPL` and cannot hit a v1 `AAPL` cache key.
   - Add a store test proving successful live stock v2 output schedules cache storage with `cache_ticker="v2:AAPL"`.
   - Implement v2-specific cache key construction without changing v1 behavior.

6. v2 30-minute cache TTL.
   - Add focused tests around the cache connector/service API showing v2 stores expire at 30 minutes, while existing v1 TTL detection remains unchanged.
   - Implement an explicit v2 TTL path, preferably by adding a parameter to the existing cache store wrapper rather than adding a new table or migration.

7. Visual stream parity for live stock v2.
   - Add a v2 endpoint test where mocked analyzer output emits `answer` chunks containing a fenced `html` or `svg` block.
   - Assert SSE output includes `answer_visual_start`, `answer_visual_delta`, and `answer_visual_done`.
   - Implement visual splitting in Layer 2 or a dedicated stream adapter so `api/analyze_v2.py` remains thin.

8. Visual stream parity for v2 cache replay.
   - Add a v2 cache hit test where cached `answer_text` contains a visual fence.
   - Assert the v2 endpoint replays the same visual event sequence and still emits cache metadata.
   - Reuse existing `SemanticAnalysisCache.stream_hit_replay` behavior where possible.

9. Post-search decision progress parity.
   - Add a service-level test around `FinancialAnalyzerV2` showing that after `search_decision_meta`, it emits the v1-style search-on or database `thinking_status`.
   - Implement in `FinancialAnalyzerV2`, since v1 owns this at the service layer and this keeps the route thin.

10. Three-layer architecture guard.
    - Add `tests/architecture/test_v2_layering.py`.
    - Assert `api/analyze_v2.py` has no imports from `connectors.*`, `models.*`, or question handler implementation modules.
    - Assert v2 service/retrieval modules do not import `fastapi.*` or `starlette.*`.
    - Assert `connectors/brave_client.py` does not import `services.*` beyond the existing schema dependency decision, or document that exception if the current code keeps it.

11. PRD closeout.
    - After all green checks, update phase 7 in `.claude/plans/analyze-v2-brave-migration-prd.json`.
    - Include validation summary, red/green evidence, gates snapshot, learnings, and next-phase considerations.
    - Explicitly record skipped plain `meta` event and skipped `BRAVE_API_KEY` health/readiness item.

## Testing Strategy

Required focused checks during implementation:

- `source venv/bin/activate && PYTHONPATH=. pytest tests/test_analyze_v2_endpoint.py -v`
- `source venv/bin/activate && PYTHONPATH=. pytest tests/architecture/test_v2_layering.py -v`
- `source venv/bin/activate && PYTHONPATH=. pytest tests/test_semantic_analysis_cache.py tests/test_semantic_cache.py -v`
- `source venv/bin/activate && PYTHONPATH=. pytest tests/services/test_financial_analyzer_v2.py -v`

Required final gates:

- `source venv/bin/activate && PYTHONPATH=. pytest tests/test_analyze_v2_endpoint.py tests/architecture/test_v2_layering.py tests/test_semantic_analysis_cache.py tests/test_semantic_cache.py tests/services/test_financial_analyzer_v2.py tests/test_healthcheck.py -v`
- `source venv/bin/activate && ruff check .`

If runtime permits, also run:

- `source venv/bin/activate && PYTHONPATH=. pytest tests/services/ tests/test_healthcheck.py -q`

## Unresolved Questions

- Whether the skipped plain `meta` event should be removed from the PRD permanently or deferred to a later observability phase.
- Whether `BRAVE_API_KEY` readiness should become a deploy-only check or a v2-specific endpoint in a later phase.
