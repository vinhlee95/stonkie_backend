# Search Performance: Phase 2 & 3 (TODO)

## Phase 2: Replace `:online` with Separate Search API

**Goal**: When search IS needed, use a fast dedicated search API (~1-2s) instead of OpenRouter's `:online` (~20s+).

1. Add `services/web_search.py` — Serper/Brave/Google Custom Search API wrapper
2. `ai_models/openrouter_client.py` — stop appending `:online`
3. `services/question_analyzer/handlers.py` — inject search results into prompt as context
4. Adapt citation handling (currently relies on OpenRouter `url_citation` annotations)

**Impact**: Search-required queries ~25s → ~5-7s

## Phase 3: Pipeline Parallelization

1. Run search decision + question classification in parallel (`asyncio.gather`)
2. Run web search in parallel with DB data fetch

**Impact**: Additional ~2-3s savings

## Open Questions
- Which search API? Serper ($50/mo/100k) vs Google Custom Search ($5/1k) vs Brave Search (free tier)
- Keep `:online` as fallback or fully remove?
