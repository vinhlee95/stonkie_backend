# /analyze v2 — Brave-Backed Retrieval Migration

## Summary

Build a parallel `POST /api/v2/companies/{ticker}/analyze` endpoint that replaces OpenRouter `:online` retrieval with the market-recap pattern: Brave Search (with goggle + market-keyed allowlist) → ranked top-K passages → LLM stuff. Mirrors recap reliability layers (goggle, allowlist, ranking, dedupe, persistence) and adopts recap's `source_id` + per-paragraph citation schema. v1 stays mounted; frontend cuts over fully to v2.

Justification: eval at `tmp/analyze_eval/full01` — Brave wins 12/15 prompts in blind LLM-judge A/B, dominates on apparent_accuracy (3.73 vs 2.73) and source_quality (4.0 vs 1.5), runs faster (p50 5.9s vs 8.8s), and produces real URLs vs `:online`'s 100% opaque vertex redirects.

## Locked decisions (from Q&A)

| # | Decision |
|---|---|
| Q1 | New endpoint `POST /api/v2/companies/{ticker}/analyze`. Frontend cuts over fully. |
| Q2 | Scope = **everything**: `GeneralFinanceHandler`, `CompanyGeneralHandler`, `CompanySpecificFinanceHandler`, `CompanyComparisonHandler`, `ETFAnalyzer` + ETF handlers. |
| Q3 | Market detection from `Company.country` (`CompanyFundamental.data["country"]`). Normalize country→market: US/USA/United States→`US`, Vietnam→`VN`, Finland→`FI`, else `US`. Ticker-less questions → fall back to question-language detection (Vietnamese→`VN`), else `US`. |
| Q4 | **No freshness gate.** Goggle (boost allowlist + discard reddit/x/twitter/youtube) + ranking handle recency. |
| Q5 | On Brave 0-results or error: **surface the error** to the user. No silent `:online` fallback. |
| Q6 | Market-recap citation pattern: stable `source_id` + per-paragraph `Citation(source_id=...)`. **No** `[SOURCES_JSON]` end-block. Frontend will be updated to render inline. |
| Q7 | Pre-retrieval: emit `thinking_status("Searching the web...")` immediately. Post-retrieval: for each **allowlisted (trusted)** source, emit `thinking_status("Reading {publisher}...")` sequentially, before generation. Non-allowlisted: silent. |
| Q8 | **No DB persistence.** Emit one structured JSON log line per request keyed by `analyze_request_id` (UUID4) containing query, market, goggle, ranked URLs, selected passage URLs, latencies. `analyze_request_id` also emitted as the first SSE event so the frontend can attach it to the rendered answer for debugging. |
| Q9 | Keep v1 indefinitely (no deprecation marker yet); v2 **forks** code rather than parameterizing v1. Decision on v1 removal deferred. |

## Design

### Module layout (parallel to `services/market_recap/`)

```
services/analyze_retrieval/        # new — Brave retrieval for chat
  __init__.py
  brave_client.py                  # thin wrapper; reuses existing recap brave_client where possible
  goggle.py                        # builds chat-tuned goggle (boost market allowlist, discard reddit/x/etc)
  market.py                        # country→market normalizer + question-language detection
  publisher.py                     # domain → human label ("reuters.com" → "Reuters")
  ranking.py                       # rank by allowlist boost + content length + score from brave
  retrieval.py                     # orchestrator: query → brave → dedupe → rank → top_k
  observability.py                 # structured JSON log per request (request_id + query + sources + latencies)
  schemas.py                       # AnalyzeRetrievalResult, AnalyzeSource, etc.

services/financial_analyzer_v2.py  # new
services/etf_analyzer_v2.py        # new
services/question_analyzer/handlers_v2.py  # new — forked handlers using passages
services/question_analyzer/comparison_handler_v2.py  # new
```

Reused as-is: `SemanticAnalysisCache`, `QuestionClassifier`, `SearchDecisionEngine`, conversation history layer, `VisualAnswerStreamSplitter`, `services/market_recap/source_policy.py` (allowlist + `is_allowlisted` + `registrable_domain`), `services/market_recap/url_utils.py` (`source_id_for`, `canonicalize_url`).

### Data model

Recap's `Source` and `Citation` schemas are the template. Add to `services/analyze_retrieval/schemas.py`:

```python
class AnalyzeSource(BaseModel):
    id: str               # source_id_for(canonical_url)
    url: str
    title: str
    publisher: str        # human label
    published_at: datetime | None
    fetched_at: datetime
    is_trusted: bool      # is_allowlisted(url, market=...)
    snippet: str

class AnalyzeRetrievalResult(BaseModel):
    candidates: list[Candidate]   # reuse recap Candidate
    sources: list[AnalyzeSource]
    market: str
    query: str
    stats: RetrievalStats         # reuse recap RetrievalStats
```

### Retrieval flow

```
question + ticker
  → market = resolve_market(ticker, question_text)
  → query = question_text  (verbatim, no rewriter — eval-locked)
  → brave_search(query, market=market, goggle=build_goggle(market))
       freshness: NONE
       country/search_lang: per market (US/en, VN/vi, FI/en)
       count: 20
  → dedupe by canonical_url
  → drop candidates with empty raw_content
  → rank: allowlisted boost > content length > brave score
  → take top_k=5
  → return AnalyzeRetrievalResult
```

If `len(top_k) == 0` → raise `BraveRetrievalError`; v2 endpoint surfaces error event to client (Q5).

### Goggle (chat-tuned)

Clone `_build_goggle` from recap but:
- Same `$boost=3,site=<allowlist>` lines per market
- Same `$discard=reddit.com|x.com|twitter.com|youtube.com`
- Add `$boost=2,site=sec.gov` and `$boost=2,site=*.gov` for US (already in allowlist but reinforce)
- For markets without an allowlist (`country` unknown), use union of all allowlists as the boost set

### Handler rewrite pattern

Each v2 handler:
1. Resolve market, run retrieval (or fail fast).
2. Emit `thinking_status` events — one for "Searching..." (already done by analyzer before handler), then one per trusted source: `"Reading {publisher_label}..."`.
3. Build prompt with passages stuffed under `[N]` headers + instruction: "Cite using `[N]` inline; every claim must reference at least one `[N]`."
4. Stream model output; transform `[N]` → recap-style paragraph events.
5. Yield: per-paragraph `answer` + `paragraph_sources` events using `source_id`.
6. After stream: emit final `sources` event with full `AnalyzeSource[]`.

### Streaming event schema (v2)

```jsonc
{ "type": "meta", "body": {"request_id": "8f1c..."} }   // ALWAYS first, even on no-search path
{ "type": "thinking_status", "body": {"text": "Searching the web...", "phase": "search"} }
{ "type": "thinking_status", "body": {"text": "Reading 5 sources: Reuters, CNBC, FT, Bloomberg, SEC", "phase": "search"} }

{ "type": "search_decision_meta", "body": { ...same as v1 ... } }

// Raw LLM output streamed token-by-token. [N] markers PRESERVED. No buffering, no stripping.
{ "type": "answer", "body": "Boeing's leverage [3][5] " }
{ "type": "answer", "body": "remains elevated..." }

// Single sources event AFTER stream ends. N -> metadata mapping. Only cited sources included.
{ "type": "sources", "body": {
    "sources": [
      {"n": 1, "id": "s_1", "url": "...", "title": "...", "publisher": "Reuters",
       "published_at": "2026-04-22T...", "is_trusted": true},
      ...
    ]
  } }

{ "type": "model_used", "body": "google/gemini-3-flash-preview" }
{ "type": "related_question", "body": "..." }
```

The frontend renders `[N]` -> citation badges using the final `sources` event. Until that event arrives, raw `[N]` is briefly visible (acceptable — FE can show a placeholder pill).

`paragraph_done` event is **not** emitted (dropped from schema).

When `SearchDecisionEngine` says no_search: only `meta` + `answer` chunks + `model_used` + `related_question` are emitted. No `thinking_status`, no `sources` event.

### Error model

Single new exception `BraveRetrievalError` raised on:
- HTTP error from Brave
- 0 results after dedupe + non-empty filter
- Unparseable response

v2 endpoint catches and emits:
```jsonc
{ "type": "error", "body": { "code": "retrieval_failed", "message": "..." } }
```
then closes the stream cleanly. Frontend surfaces the error.

### Observability (no DB)

No `analyze_retrieval_snapshots` table. Debug linkage is done via structured logs:

1. Mint `analyze_request_id = uuid4()` at top of v2 endpoint.
2. Emit it as first SSE event: `{"type": "meta", "body": {"request_id": "..."}}`. Frontend attaches it to the rendered answer (enables "report bad answer" → log lookup by request_id).
3. After retrieval succeeds, `observability.log_retrieval(...)` writes one JSON line at INFO:
   ```jsonc
   {
     "event": "analyze_v2_retrieval",
     "request_id": "...",
     "conversation_id": "...",
     "ticker": "BA",
     "question": "...",
     "market": "US",
     "goggle_hash": "...",
     "ranked_urls": ["https://reuters.com/...", ...],
     "selected_source_ids": ["s_1", "s_2", ...],
     "selected_urls": [...],
     "brave_latency_ms": 1240,
     "total_retrieval_latency_ms": 1380,
     "is_trusted_count": 3
   }
   ```
4. Logger is the existing app logger; GCP Logs becomes the query interface (filter by `jsonPayload.request_id`).
5. **No raw Brave response is logged** — only ranked URLs + selected passage IDs. Keeps log volume sane and avoids storing arbitrary third-party content.

Revisit DB persistence only if we later need offline replay/eval against historical snapshots.

### Caching

`SemanticAnalysisCache` reused unchanged. Key already includes ticker + question; v2 hits/misses live in the same cache namespace as v1 — be aware that a v1 cache hit could replay a v1-shaped payload to a v2 client. **Add a cache namespace bump** (e.g. cache key prefix `v2:`) so v2 misses on v1 entries and vice versa.

## Implementation steps (ordered)

### Phase A — Retrieval foundation (no endpoint yet)

1. **Create `services/analyze_retrieval/` skeleton** with empty modules + `__init__.py`.
2. **`market.py`**: `resolve_market(ticker_country: str | None, question_text: str | None) -> str`. Country normalization table + Vietnamese language detection (heuristic: presence of Vietnamese diacritics in question). Unit tests.
3. **`publisher.py`**: `publisher_label_for(url: str) -> str`. Lookup table for big publishers; fallback to `registrable_domain` titlecased.
4. **`goggle.py`**: `build_chat_goggle(market: str) -> str`. Reuse market allowlist; same discards as recap. Unit tests parity-checked against recap goggle for matching markets.
5. **`schemas.py`**: `AnalyzeSource`, `AnalyzeRetrievalResult`, `BraveRetrievalError`.
6. **`brave_client.py`**: thin wrapper. Reuse `services/market_recap/brave_client.BraveClient` directly with `freshness=None` if it accepts that; otherwise add a `search_no_freshness()` method to the recap client (preferred: extend recap client with optional freshness, since chat needs it). Confirm with user before extending recap module.
7. **`ranking.py`**: `rank_for_chat(candidates, market) -> list[Candidate]`. Rule: allowlisted first, then content length desc, then brave score desc.
8. **`retrieval.py`**: `retrieve_for_analyze(question, market, top_k=5) -> AnalyzeRetrievalResult`. Orchestrate. Raise `BraveRetrievalError` on empty result.
9. **`observability.py`**: `log_retrieval(request_id, ...)` — single JSON-line emitter via app logger. No DB, no migration.
10. **Integration test**: hit retrieval against canned Brave responses (fixtures from eval `tmp/analyze_eval/full01/<id>/brave.json` raw extracts). No live API.

### Phase B — Handler v2 + analyzer v2 (offline)

11. **`handlers_v2.GeneralFinanceHandlerV2`**: takes passages, builds stuffed prompt with `[N]` instruction, streams, transforms to paragraph_done events.
12. **`handlers_v2.CompanyGeneralHandlerV2`**: same pattern, with company name in prompt.
13. **`handlers_v2.CompanySpecificFinanceHandlerV2`**: ports the data-augmented prompt (financials from DB) + Brave passages.
14. **`comparison_handler_v2.CompanyComparisonHandlerV2`**: retrieves once per ticker (or single combined query — TBD, see unresolved), then stuffs.
15. **`financial_analyzer_v2.FinancialAnalyzerV2.analyze_question(...)`**: top-level orchestration. Calls classifier (reused), runs retrieval if `use_google_search=True`, dispatches to v2 handlers, emits `thinking_status` events including the "Reading {publisher}" sequence.
16. **`etf_analyzer_v2.ETFAnalyzerV2`**: same shape adapted to ETF prompts.
17. **Unit tests** per handler with canned passages.

### Phase C — Wire up endpoint

18. **`main.py`**: add `POST /api/v2/companies/{ticker}/analyze`, parallel to v1. Reuses cookie / conversation / cache flow; only difference is `analyzer = etf_analyzer_v2 if is_etf else financial_analyzer_v2`.
19. **Bump `SemanticAnalysisCache` key namespace** to `v2:` for v2 to avoid cross-version replay.
20. **Error stream contract**: ensure `{"type": "error", ...}` is emitted before stream close on `BraveRetrievalError`.
21. **Healthcheck** that v2 endpoint is reachable + Brave key validated at startup (fail fast in deploy if missing).

### Phase D — Validation

22. Run the existing eval harness (`scripts/eval_analyze_search/run_eval.py`) against the v2 endpoint instead of the synthetic brave arm — confirms parity with the eval that justified this work.
23. Manual smoke: 5 prompts across categories via `curl` to the v2 endpoint, verify SSE event sequence + citation rendering shape.
24. Latency check: v2 p50/p95 vs v1, capture in PR description.
25. Run `pytest` + `ruff check`.

### Phase E — Frontend handoff

26. Document the v2 SSE event schema (above) for frontend.
27. Frontend renders `paragraph_done.source_ids` as inline citation badges keyed to the final `sources` event.
28. Frontend swaps endpoint URL once renderer ships.

## Testing strategy

- **Unit**: market resolution, goggle building, ranking, publisher labeling, `[N]`→`source_id` post-processor (offline, deterministic).
- **Integration**: retrieval against Brave fixtures (no live calls). Handler tests with canned passages.
- **End-to-end**: re-run `scripts/eval_analyze_search/run_eval.py` with a third `--arm v2-endpoint` that POSTs to the new endpoint. Compare against the captured `brave` arm — should be near-identical answers, since the underlying pipeline is the same.
- **Manual**: 5 smoke prompts × SSE inspection.
- **No tests hit OpenRouter or Brave live** (per memory: `feedback_no_external_api_tests`).

## Order of work

1. Phase A steps 1–10 (retrieval foundation + persistence + offline tests).
2. Phase B steps 11–17 (one handler at a time; start with `CompanyGeneralHandlerV2` since it has the simplest prompt).
3. Phase C steps 18–21 (endpoint).
4. Phase D steps 22–25 (validation).
5. Phase E steps 26–28 (FE handoff; non-blocking for backend merge).

Per-handler PRs in Phase B are recommended over one giant PR.

## Unresolved questions

- **Q6.1** Strip `[N]` markers from visible answer text, or pass through for FE to render? Default: strip backend-side, FE renders citation badges from `paragraph_done.source_ids`. Confirm with FE.
- **Q14.1** Comparison handler: one Brave query per ticker (N×latency, N×5 sources) or one combined query? Default: per ticker.
- **Q6.2** ETF handler vs ETF context_builders: ETF has its own context_builders module; how much of that survives? Likely all of it — Brave just augments the existing context.
- **Q6.3** `CompanySpecificFinanceHandler` already injects DB financials into the prompt. With Brave passages added on top, does prompt size blow past model context? Default: trim raw_content to 3000 chars per passage when DB context is also large.
- **Q6.4** Should v2 retain the `:online` path as a hidden flag for ops/debug, even if frontend never calls it? Default: no — clean fork.
- **Q3.1** Country normalization table — we know "USA" / "Vietnam" / "Finland". What other countries appear in `company_fundamental.data["country"]`? Quick query needed before finalizing the table.
