# Latency Audit: /analyze Endpoint

**Date:** 2026-04-05
**Ticker:** AAPL | **Question:** "What is Apple's revenue trend over the last 3 years?"
**Model:** `fastest` (google/gemini-2.5-flash:nitro) | **Handler:** COMPANY_SPECIFIC_FINANCE
**Search decision:** ON (fallback — Sonnet 4.6 classifier timed out)

---

## End-to-End Timeline (23.46s total)

| # | Step | Duration | % of Total | Notes |
|---|------|----------|------------|-------|
| 1 | Request preprocessing | 0.126s | 0.5% | Body parse + ticker norm + ETF check + Redis |
|   | — ETF check | 0.124s | — | DB lookup for ETF detection |
|   | — Conversation history (Redis) | 0.001s | — | Fast, no history existed |
| 2 | get_available_periods_and_metrics | 0.278s | 1.2% | DB metadata for search decision context |
| 3 | **PARALLEL: classify + search decision** | **5.250s** | **22.4%** | **Wall-clock of asyncio.gather()** |
|   | — classify_question_type | 5.247s | — | Gemini 3.0 Flash LLM |
|   | —— ticker_extractor.extract_tickers | 4.256s | — | **LLM call to detect comparisons** |
|   | —— LLM classification itself | ~0.99s | — | Fast after ticker extraction |
|   | — search_decision_engine.decide | 5.250s | — | **Sonnet 4.6 TIMED OUT (5s limit)** |
| 4 | classify_data_requirement | 1.017s | 4.3% | Gemini 3.0 Flash LLM (sequential) |
| 5 | classify_period_requirement | 1.035s | 4.4% | Gemini 3.0 Flash LLM (sequential) |
| 6 | **PARALLEL: dimensions + data fetch** | **2.412s** | **10.3%** | Wall-clock of asyncio.gather() |
|   | — _analyze_question_dimensions | 2.257s | — | Gemini 3.0 Flash LLM (3s timeout) |
|   | — fetch_optimized_data (DB) | 0.154s | — | 3 annual statements from PostgreSQL |
| 7 | _build_financial_context | 0.001s | 0.0% | String assembly, negligible |
| 8 | **LLM generation (TTFT)** | **7.848s** | **33.4%** | **Time to first token from model** |
| 9 | LLM generation (streaming) | 4.471s | 19.1% | Token streaming after first token |
|   | — model_generate_content total | 12.319s | — | TTFT + streaming combined |
| 10 | related_questions | 1.139s | 4.9% | Additional LLM call (sequential, post-answer) |
|   | **TOTAL** | **23.459s** | **100%** | |

## Client-Perceived Latency

| Metric | Duration |
|--------|----------|
| HTTP TTFB (first byte) | 0.128s |
| First `thinking_status` event | 0.128s |
| First `search_decision_meta` event | ~5.4s |
| **First `answer` token (user sees text)** | **~17.7s** |
| Answer streaming duration | ~4.1s |
| Last answer to related_questions | ~1.1s |
| HTTP total time | 23.59s |

---

## Top Bottlenecks (sorted by impact)

### 1. LLM Generation TTFT — 7.85s (33.4%)
**What:** Time from prompt submission to first token back from Gemini 2.5 Flash `:nitro:online` (with Google Search enabled).
**Why slow:** The `:online` variant performs a web search before generating, adding significant latency. The prompt is also large (financial context + statements + instructions).
**Optimization opportunities:**
- **Skip Google Search when DB data is sufficient** — the search decision timed out and fell back to `search=ON`. If it had succeeded, it would likely have returned `search=OFF` (DB has AAPL data for 2023-2025), avoiding the `:online` penalty entirely. Estimated saving: **3-5s**.
- **Reduce prompt size** — trim verbose instructions, compress financial data format.
- **Consider streaming-first architecture** — start yielding partial results earlier.

### 2. ticker_extractor.extract_tickers — 4.26s (18.1%)
**What:** LLM call to detect if the question mentions multiple tickers (comparison detection).
**Why slow:** This is an LLM call (Gemini 3.0 Flash) that runs _before_ the main classification. For non-comparison questions (vast majority), it's pure overhead.
**Optimization opportunities:**
- **Regex/keyword pre-filter** — check for common comparison patterns ("vs", "compare", "versus", multiple $TICKER mentions) before calling LLM. Skip LLM if no comparison signals found. Estimated saving: **3-4s for ~90% of questions**.
- **Move to parallel** — run ticker extraction concurrently with classification instead of sequentially inside `classify_question_type`.

### 3. search_decision_engine.decide — 5.25s (timed out)
**What:** Sonnet 4.6 LLM call to decide whether Google Search is needed. Timed out at 5s limit.
**Why slow:** Sonnet 4.6 is a larger, slower model. The call runs via `asyncio.to_thread` (synchronous OpenRouter call wrapped in thread).
**Optimization opportunities:**
- **Switch to Gemini 3.0 Flash** — the search decision prompt is simple classification, doesn't need Sonnet quality. Would likely complete in <1s. Estimated saving: **4s+ (eliminates timeouts)**.
- **Expand keyword fast-path** — more patterns to skip LLM entirely (e.g., "revenue trend" with existing DB data → no search needed).
- **Reduce timeout** — 5s is too generous; 3s with fail-safe would still catch most cases.

### 4. classify_data_requirement + classify_period_requirement — 2.05s (8.7%)
**What:** Two sequential Gemini 3.0 Flash LLM calls to determine what data to fetch and for which periods.
**Why slow:** They run sequentially but could be combined or parallelized.
**Optimization opportunities:**
- **Merge into single LLM call** — one prompt that returns both data level AND period requirement. Estimated saving: **~1s**.
- **Run in parallel** — they're independent of each other. Estimated saving: **~1s**.

### 5. _analyze_question_dimensions — 2.26s (9.6%)
**What:** LLM call to generate section titles for the answer structure.
**Why slow:** Full LLM call for what could be a simpler decision.
**Optimization opportunities:**
- **Use cheaper/faster model** — Gemini 2.5 Flash instead of 3.0 Flash for this simple task.
- **Cache common patterns** — revenue trend, profit margin, etc. are repeated question types.
- **Skip for simple questions** — use default sections unless deep_analysis=true.

### 6. LLM streaming duration — 4.47s (19.1%)
**What:** Time to stream the full answer after first token.
**Why:** Model generates ~1000 tokens including a Chart.js visualization. This is largely irreducible.
**Optimization opportunities:**
- **Generate chart separately** — don't include chart generation in main answer prompt; generate it as a follow-up or client-side. Would reduce token count.

### 7. related_questions — 1.14s (4.9%)
**What:** Post-answer LLM call to generate follow-up questions.
**Optimization opportunities:**
- **Run in parallel with answer streaming** — start generating related questions as soon as classification is known, not after answer completes. Estimated saving: **~1s of perceived latency**.

---

## Summary: Optimization Impact Matrix

| Optimization | Estimated Saving | Effort | Impact |
|-------------|-----------------|--------|--------|
| Regex pre-filter for ticker extraction | 3-4s | Low | **HIGH** |
| Switch search decision to Gemini Flash | 4s+ | Low | **HIGH** |
| Merge data_req + period_req classifiers | ~1s | Medium | Medium |
| Fix search decision → skip Google Search when DB sufficient | 3-5s | Low (fix timeout) | **HIGH** |
| Parallelize related_questions with streaming | ~1s perceived | Medium | Medium |
| Skip _analyze_question_dimensions for simple questions | ~2s | Low | Medium |
| Reduce prompt size for main generation | 1-2s TTFT | Medium | Medium |
| Cache common dimension patterns | ~2s | Medium | Medium |

### Quick Wins (low effort, high impact):
1. **Regex pre-filter for ticker extraction** — skip LLM for non-comparison questions
2. **Switch search decision model to Gemini Flash** — eliminates 5s timeouts
3. **Fix search decision reliability** — prevents unnecessary Google Search `:online` penalty

### Combined potential improvement:
Current: **~17.7s** to first answer token, **~23.5s** total
With quick wins: **~8-10s** to first answer token, **~15s** total (~40% reduction)

---

## Raw Pipeline Trace

```
T+0.000s  Request received
T+0.126s  Preprocessing done (ETF check: 0.124s, Redis: 0.001s)
T+0.404s  DB metadata fetched (periods + metrics: 0.278s)
T+0.404s  START parallel: classify_question_type + search_decision
T+4.660s    ticker_extractor.extract_tickers done (4.256s) ← BOTTLENECK
T+5.651s    classify_question_type done (5.247s)
T+5.654s    search_decision timed out (5.250s) ← BOTTLENECK
T+5.654s  END parallel (wall-clock: 5.250s)
T+6.671s  classify_data_requirement done (1.017s)
T+7.706s  classify_period_requirement done (1.035s)
T+7.706s  START parallel: dimensions + data_fetch
T+7.860s    fetch_optimized_data done (0.154s)
T+9.963s    _analyze_question_dimensions done (2.257s)
T+10.118s END parallel (wall-clock: 2.412s)
T+10.118s _build_financial_context done (0.001s)
T+10.118s START LLM generation (Gemini 2.5 Flash :nitro:online)
T+17.966s   First token received (TTFT: 7.848s) ← BOTTLENECK
T+22.437s   Streaming complete (total generation: 12.319s)
T+22.437s START related_questions
T+23.577s   Related questions done (1.139s)
T+23.577s TOTAL: 23.459s
```
