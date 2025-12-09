# OpenRouter Latency Check

Measure time-to-first-token (TTFT) and total latency for a challenging prompt without touching the main app.

## Prerequisites
- `OPENROUTER_API_KEY` (required for OpenRouter)
- `OPENROUTER_MODEL` (optional, defaults to `openrouter/google/gemini-2.0-flash-001`)
- Optional direct comparisons:
  - `OPENAI_API_KEY` for `--provider openai`
  - `GEMINI_API_KEY` for `--provider gemini`

## Run examples
```bash
# OpenRouter (default)
OPENROUTER_API_KEY=... python scripts/openrouter_latency_check.py \
  --prompt "Analyze Nvidia's competitive moat vs AMD and Intel; include key risks and catalysts in under 220 words."

# OpenRouter with explicit model
OPENROUTER_API_KEY=... python scripts/openrouter_latency_check.py \
  --model openrouter/google/gemini-2.0-flash-001

# Direct OpenAI comparison (if key present)
OPENAI_API_KEY=... python scripts/openrouter_latency_check.py --provider openai --model gpt-4.1-mini

# Direct Gemini comparison (if key present)
GEMINI_API_KEY=... python scripts/openrouter_latency_check.py --provider gemini --model gemini-2.0-flash-001
```

Outputs include TTFT and total stream time, helping you compare OpenRouter vs direct provider latency.


