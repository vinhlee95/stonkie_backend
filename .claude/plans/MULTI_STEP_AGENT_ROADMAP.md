# Multi-Step Agent Roadmap

Notion ticket: [Execute multi-step plan with follow-up](https://www.notion.so/330c1f5aa0548038bb96c8a4720b46e9)

End goal: agent that can plan, execute multi-step analysis, use tools, and interact with users mid-flow.

---

## Slice 1: Multi-Ticker Stock Comparison
**Status**: In progress
**What**: Extend classify→fetch→answer pipeline to handle 2-4 stock tickers in one request.
**Pattern**: Mirror existing ETF comparison (ticker extraction → parallel fetch → comparison prompt → stream).
**New files**: `ticker_extractor.py`, `comparison_builder.py`, `comparison_handler.py`
**Modified**: `types.py`, `classifier.py`, `financial_analyzer.py`

### Definition of Done
| Test Question | Expected Behavior |
|---|---|
| `"Compare AAPL and MSFT margins"` (ticker=AAPL) | Extracts [AAPL, MSFT], per-ticker thinking_status, side-by-side comparison, sources with `"database"` type |
| `"NVDA vs AMD revenue growth"` (ticker=NVDA) | Extracts [NVDA, AMD], streams revenue growth comparison |
| `"Compare this stock with GOOGL"` (ticker=TSLA) | Resolves "this stock" → TSLA, compares TSLA vs GOOGL |
| `"What is Apple's revenue?"` (ticker=AAPL) | No comparison detected, routes to normal single-ticker handler |
| `"Compare Apple and Microsoft"` (ticker=none) | AI fallback resolves names → [AAPL, MSFT], streams comparison |
| Ticker not in DB | Falls back, no comparison if <2 valid tickers |
| Ticker in DB but no financials | Source = `"training_data"`, LLM states caveat |
| Related questions generated | 2-3 comparison-specific follow-ups |

---

## Slice 2: Conversational Follow-Up for Comparisons
**Status**: Not started
**What**: After a comparison, user can refine via follow-up messages using existing conversation infra. Agent suggests options in text, user replies next turn.

### Definition of Done
| Test Question | Expected Behavior |
|---|---|
| Turn 1: `"Who are NVDA's main competitors?"` | Lists competitors, mentions which have data in our system |
| Turn 2: `"Compare NVDA with AMD and INTC"` | Conversation context + 3 tickers → 3-way comparison |
| Turn 2 alt: `"Now compare their margins"` | Sticky routing reuses comparison context |
| Turn 1: `"Compare AAPL and MSFT"` → Turn 2: `"Add GOOGL"` | Previous tickers + GOOGL → 3-way comparison |

**Key changes**: Extend sticky routing to `COMPANY_COMPARISON`. Store compared tickers in conversation meta.

---

## Slice 3: Tool Use (Function Calling) via OpenRouter
**Status**: Not started
**What**: Enable LLM to call tools instead of pre-fetching all data.

**Tools**: `fetch_fundamentals(ticker)`, `fetch_quarterly_financials(ticker, num_periods)`, `fetch_annual_financials(ticker, num_periods)`, `list_companies(sector?)`, `search_web(query)`

### Definition of Done
| Test Question | Expected Behavior |
|---|---|
| `"What's AAPL's revenue?"` (no pre-fetch) | LLM calls `fetch_fundamentals("AAPL")`, answers from result |
| `"Compare AAPL and MSFT last 3 quarters"` | LLM calls `fetch_quarterly_financials` for both, synthesizes |
| `"Which tech companies do we have?"` | LLM calls `list_companies(sector="Technology")` |
| Tool calls visible in stream | `thinking_status`: "Fetching AAPL financials..." |
| Tool failure | LLM handles gracefully, tries alternative or explains |

---

## Slice 4: Planning & Decomposition
**Status**: Not started
**What**: LLM breaks complex questions into sub-steps before executing (requires Slice 3).

### Definition of Done
| Test Question | Expected Behavior |
|---|---|
| `"Compare NVDA's competitors' margins over last 3 quarters"` | Plan: identify competitors → fetch quarterly data → compute margins → compare. Executes all, streams synthesis. |
| `"How does TSLA compare to traditional automakers?"` | Plan: identify automakers in DB → fetch → compare efficiency |
| Plan visible in stream | `plan_step` events: "Step 1/3: Identifying competitors..." |
| Incomplete data | Agent notes missing data, adjusts comparison |

---

## Slice 5: Interactive Mid-Flow Choices
**Status**: Not started
**What**: Agent pauses mid-plan to propose options. New SSE event + frontend UI (requires Slice 4).

### Definition of Done
| Test Question | Expected Behavior |
|---|---|
| `"Compare NVDA's competitors"` | Streams `user_choice` event with competitor checkboxes |
| User selects AMD + INTC | Agent resumes, fetches selected, streams comparison |
| No selection / timeout | Agent proceeds with top 3 (graceful fallback) |
| Frontend renders choice UI | Selectable chips appear inline |

---

## Slice 6: Computation & Charts
**Status**: Not started
**What**: Compute derived metrics, generate charts (requires Slice 3).

### Definition of Done
| Test Question | Expected Behavior |
|---|---|
| `"Plot AAPL vs MSFT revenue over 5 years"` | Line chart comparing revenue trends |
| `"Gross margin trend for NVDA?"` | Computes from raw statements, shows table + chart |
| Chart in stream | `chart` event, frontend renders via chart.js |

---

## Dependency Graph
```
Slice 1 (comparison)     ← standalone
Slice 2 (follow-up)      ← requires Slice 1
Slice 3 (tool use)       ← standalone, enhances Slice 1+2
Slice 4 (planning)       ← requires Slice 3
Slice 5 (mid-flow choice)← requires Slice 4 + frontend
Slice 6 (compute/charts) ← requires Slice 3 + frontend
```
