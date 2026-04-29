# Analyze Endpoint: Brave→LLM vs OpenRouter `:online` Eval

Throwaway harness to decide if `/analyze` should swap `:online` for the market-recap-style Brave→LLM retrieval pipeline.

## Goal

For 10–20 curated prompts, run both retrieval strategies through the same answer-generation step and compare on: **relevance, helpfulness, accuracy (spot-check), source quality, latency, cost**.

Out of scope: productionisation, caching layer, frontend changes. This is a decision-support harness only.

## Two Arms

| Arm | Retrieval | Generation |
|---|---|---|
| `online` | Implicit — provider-side web search | OpenRouter `<model>:online`, capture `url_citation` annotations |
| `brave` | `BraveClient.search()` (reuse `services/market_recap/brave_client.py`) → top-K passages stuffed into prompt | Same OpenRouter model **without** `:online`, citations injected as `[N]` refs |

Both arms must use the **same base model** (the one `/analyze` currently uses) and the **same answer prompt template**, so the only variable is retrieval. No `_process_source_tags` shenanigans — the harness just records final text + citations.

## Prompt Set (15 prompts target)

Cover the categories `question_analyzer` already recognises, weighted toward company-specific:

- **Company-specific finance (5)** — e.g. "What was AAPL's FCF margin in latest 10-Q?", "Did NVDA beat consensus last quarter?", "How leveraged is BA right now?", "What are MSFT's segment revenues YTD?", "PLTR's stock-based comp trend"
- **Company general (4)** — e.g. "What does Snowflake actually do?", "Who is TSLA's biggest competitor?", "What's the bull case for SHOP?", "Recent management changes at INTC"
- **Breaking/market news (3)** — e.g. "What moved markets this week?", "Latest on Fed rate decision", "Any major M&A this week"
- **ETF / sector (2)** — e.g. "How is SMH performing vs SOXX YTD?", "Best dividend ETFs right now"
- **Ambiguous / non-English (1)** — e.g. a Vietnamese-language ticker question to stress non-EN behaviour

Freeze prompts in `scripts/eval_analyze_search/prompts.json` with `id`, `category`, `text`, optional `ticker`. Pin the run date so "this week" prompts are reproducible.

## Script

`scripts/eval_analyze_search/run_eval.py`

For each prompt × arm:
1. Time retrieval (Brave only) and generation separately.
2. Capture: `final_text`, `citations[]` (url, title, domain, published_at), `model`, `tokens_in/out`, `latency_ms`, `error?`.
3. Write artifact `tmp/analyze_eval/<run_id>/<prompt_id>/<arm>.json`.

Reuse from market-recap:
- `BraveClient` (skip allowlist + window filter — `/analyze` is global, evergreen-friendly).
- The `_serialize` / `_write_json` helpers in `compare_us_recap_providers.py`.

Do **not** call the live `/analyze` endpoint — call its core function directly so we can swap retrieval cleanly. Likely entry: a thin wrapper that mirrors `financial_analyzer.analyze_question` but takes `retrieval_mode: Literal["online", "brave"]`.

## Judging

Four axes, different methods per axis (don't pretend one method covers all):

1. **Relevance + helpfulness** → LLM-as-judge, blind A/B, **swap order to control position bias**, run on all prompts. Judge model: Claude Opus, prompt asks for per-axis 1–5 + short reason. Output: `tmp/analyze_eval/<run_id>/judgements.json`.
2. **Accuracy / factuality** → manual spot-check on 5 prompts (skewed to company-specific finance, since that's where hallucinations hurt most). No LLM judge — record findings in a markdown scratchpad.
3. **Source quality** → programmatic per arm: unique-domain count, % allowlisted (reuse `is_allowlisted` for the news subset), % with `published_at`, freshness (median age vs run date), dead-link rate (HEAD request).
4. **Performance / cost** → p50 + p95 latency per arm, total tokens, $ per query at current OpenRouter rates.

## Artifacts

```
tmp/analyze_eval/<run_id>/
  prompts.json                      # snapshot of inputs
  <prompt_id>/
    online.json                     # answer + citations + timings + tokens
    brave.json                      # same, plus retrieval debug (top-K, queries, scores)
  judgements.json                   # LLM judge output
  source_quality.csv                # per-arm aggregates
  summary.md                        # human-readable verdict, bucketed by category
```

Bucket the summary by category — the live hypothesis is that `:online` wins on breaking news and Brave wins on company-specific finance.

## Order of work

1. Add `scripts/eval_analyze_search/prompts.json` with 15 prompts.
2. Extract or wrap the `/analyze` answer-generation core so retrieval is swappable. Verify it produces the same output as today for `online` mode against one prompt.
3. Build Brave retrieval helper for `/analyze`: **one query = user prompt verbatim** (mirrors how `:online` works today; keeps comparison fair, avoids extra LLM hop). Top-K = 5–8 passages, prompt-stuffed.
4. Write `run_eval.py` — orchestrates both arms, writes per-prompt artifacts, prints retrieval/generation timings.
5. Run end-to-end on one prompt, sanity check the JSON.
6. Run full eval (15 prompts × 2 arms = 30 generations).
7. Compute source-quality CSV + latency/cost aggregates.
8. Write LLM-judge prompt, run blind A/B with order swap, save `judgements.json`.
9. Manual accuracy spot-check on 5 company-specific prompts; record findings.
10. Write `summary.md` with per-category verdict + recommendation.

## Locked decisions

- Brave query = user prompt verbatim (no query-rewriter).
- Top-K = 5, full `raw_content` (match recap).
- No allowlisting in the Brave arm — `/analyze` legitimately spans SEC, IR, blogs.
- No "no search" baseline arm.
- `run_date` pinned in `prompts.json` for reproducibility.
