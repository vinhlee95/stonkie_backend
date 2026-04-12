# POST /analyze Endpoint Audit: RAG & DB Operations

**Date:** 2026-04-09
**Test:** `POST /api/companies/AAPL/analyze` with `"What is Apple revenue and profit margin trend over the last 3 years?"`
**Route:** COMPANY_SPECIFIC_FINANCE → DETAILED → annual, 3 periods

## Measured Timings (Real API Call)

| Phase | Duration | Cumulative | Notes |
|-------|----------|------------|-------|
| **1. Pre-handler setup** | ~0.4s | 0.4s | ETF check, conversation store (Redis), cookie |
| **2. `get_available_periods` + `get_available_metrics`** | ~0.28s | 0.7s | 2 sequential DB queries to Neon (EU), feeds search decision |
| **3. Parallel: `classify_question_type` + `search_decision`** | 1.88s | 2.6s | 2 LLM calls in parallel (Gemini 3 Flash + Gemini 2.5 Flash Nitro) |
| **4. `classify_data_requirement`** | 1.69s | 4.3s | Sequential LLM call (Gemini 3 Flash) |
| **5. `classify_period_requirement`** | 1.34s | 5.6s | Sequential LLM call (Gemini 3 Flash) |
| **6. `fetch_optimized_data` (DB)** | 0.38s | 6.0s | 3 annual statements from Neon DB |
| **7. Context building** | <0.01s | 6.0s | String formatting, negligible |
| **8. Main LLM generation (TTFT)** | 1.81s | 7.8s | Time to first token from Gemini 2.5 Flash Nitro |
| **9. Main LLM generation (full)** | 5.84s | 11.9s | Full streaming answer + charts |
| **10. Related questions** | 1.06s | 12.6s | Separate LLM call after answer completes |
| **Total (server)** | **12.63s** | | |
| **Total (client, curl)** | **13.28s** | | Includes HTTP overhead |

## Waterfall Diagram

```
0s        2s        4s        6s        8s       10s       12s
|---------|---------|---------|---------|---------|---------|
[ETF+Redis+avail_periods ]  (0.7s)
          [classify_type ||||| search_decision]  (1.88s, parallel)
                    [classify_data_req ]  (1.69s)
                              [classify_period ]  (1.34s)
                                       [DB fetch]  (0.38s)
                                        [====== Main LLM streaming ======]  (5.84s)
                                                                    [related_q]  (1.06s)
```

## DB Infrastructure

- **Host:** Neon PostgreSQL (eu-central-1, pooler endpoint)
- **Latency:** ~150-400ms per query from local dev (cross-region)
- **Tables:** `company_financial_statement` (226 rows), `company_quarterly_financial_statement` (298 rows)
- **Indexes:** Exist on `company_symbol` (B-tree) + composite unique constraints
- **JSON payload size:** ~1-1.5KB per row (income_statement, balance_sheet, cash_flow columns)
- **Cold vs warm:** First annual query ~1.8s, subsequent ~0.7s (connection pool warmup)

## Key Findings

### 1. Sequential LLM Classifier Chain is the Main Bottleneck (4.9s)

Three LLM classification calls happen mostly sequentially:
- `classify_question_type` (1.88s) — parallelized with `search_decision`, good
- `classify_data_requirement` (1.69s) — **sequential, blocks everything**
- `classify_period_requirement` (1.34s) — **sequential, blocks everything**

**Total pre-generation LLM overhead: 4.9s** (39% of total time)

### 2. `classify_data_requirement` + `classify_period_requirement` Cannot Run in Parallel Today

These are sequential because `period_requirement` is only needed for DETAILED/QUARTERLY_SUMMARY/ANNUAL_SUMMARY — the code needs `data_requirement` first to decide whether to call `classify_period_requirement`.

### 3. DB Fetch Waits for All Classifiers to Complete

`fetch_optimized_data` (0.38s) only starts after both `classify_data_requirement` AND `classify_period_requirement` finish. The DB data itself is fast but sits idle waiting ~5s.

### 4. `get_available_periods` + `get_available_metrics` Are Sequential and Block the Parallel Block

In `financial_analyzer.py:119-124`, these two queries run **sequentially before** the parallel classify+search block. They feed into the search decision engine but are not parallelized with anything.

### 5. Related Questions Are Post-Stream Blocking (1.06s)

`_generate_related_questions` runs **after** the full answer stream completes, adding 1s before the client sees the response as "done".

### 6. Each DB Query Opens a New Connection via `SessionLocal()`

Every method in `CompanyFinancialConnector` creates a new `SessionLocal()` context manager. For the DETAILED path, there are at least 4-5 separate DB sessions opened (available_periods, available_metrics, annual fetch, optional quarterly fetch, ETF check). No connection reuse across the request lifecycle.

## Improvement Opportunities (Sorted by Impact)

### High Impact

| # | Improvement | Est. Savings | Complexity |
|---|------------|-------------|------------|
| 1 | **Merge `classify_data_requirement` + `classify_period_requirement` into a single LLM call** — one prompt returning both data level and period spec. Saves one full LLM round-trip. | **1.3-1.7s** | Medium |
| 2 | **Parallelize `classify_data_requirement` with DB prefetch** — speculatively fetch last 3-5 annual + 4 quarterly statements while classifying. If classification says DETAILED, data is already ready. Discard if NONE/BASIC. | **0.3-1.7s** | Medium |
| 3 | **Fire related questions in parallel with the main LLM stream** — start generating related questions as soon as the question is known, not after the answer finishes. Or generate them client-side / from a separate async request. | **1.0s** | Low |
| 4 | **Parallelize `get_available_periods` + `get_available_metrics` with each other and with classify+search block** — currently sequential and blocking. Could run all 4 tasks (avail_periods, avail_metrics, classify, search) in a single `asyncio.gather`. | **0.2-0.3s** | Low |

### Medium Impact

| # | Improvement | Est. Savings | Complexity |
|---|------------|-------------|------------|
| 5 | **Cache `get_available_periods` / `get_available_metrics` in Redis** — these change infrequently (only when crawl jobs run). TTL of 1 hour would eliminate 2 DB queries per request. | **0.15-0.3s** | Low |
| 6 | **Use a single DB session per request** — pass a shared session through the handler chain instead of opening new `SessionLocal()` per query. Eliminates connection acquisition overhead. | **0.1-0.2s** | Medium |
| 7 | **Add `pool_pre_ping=True` and tune pool size on the SQLAlchemy engine** — reduces cold-start latency on the first query of a session (observed 1.8s vs 0.7s warm). | **0.5-1s (cold only)** | Low |

### Lower Impact / Longer Term

| # | Improvement | Est. Savings | Complexity |
|---|------------|-------------|------------|
| 8 | **Replace LLM classifiers with rule-based or lightweight local model** — keyword matching already exists for quarterly/annual report detection. Extending this pattern to data_requirement (regex + keyword scoring) could eliminate 1-2 LLM calls entirely. | **1.5-3s** | High |
| 9 | **Cache classification results** — same question+ticker → same classification. Short TTL Redis cache keyed on `hash(question, ticker)`. | **3-5s (cache hit)** | Low |
| 10 | **Column-level lazy loading for JSON fields** — if only `period_end_year` and `filing_10k_url` are needed (e.g., for summary/filing URL lookups), avoid loading full JSON blobs. Use SQLAlchemy `deferred()` or explicit column selection. | **0.05-0.1s** | Medium |

## Summary

The **#1 bottleneck is the sequential LLM classifier chain** (classify_type → classify_data → classify_period), consuming ~4.9s out of 12.6s total. DB operations themselves are relatively fast (~0.5-0.7s total across all queries) given the remote Neon DB in EU. The most impactful single change would be **merging the two classification calls into one** (saving ~1.5s) or **speculative DB prefetch** in parallel with classification.

Combined, improvements #1-#4 could reduce total time from **~12.6s to ~9-10s** (20-25% reduction) without changing the LLM generation itself.
