# Search Provider Re-evaluation — Weekly US Market Recap

## Context

The current PRD ([weekly-us-market-recap-prd.json](.claude/plans/weekly-us-market-recap-prd.json), Phase 2) plans to use **Google Custom Search JSON API (CSE)** as the retrieval layer feeding into a fetch (httpx + trafilatura) → LLM grounding pipeline. The user heard Google is retiring this API and wants to compare against Tavily, Exa, Perplexity Sonar, and Brave Search before Phase 2 implementation begins.

This document is a **decision aid only** — no code changes. Phase 1 is already complete; Phase 2 has not started, so a provider swap here is cheap.

## Google CSE — current status (verified 2026-04)

- **Closed to new customers.** New API key signups are not accepted.
- **Existing customers have until 2027-01-01** to migrate.
- The **Site Restricted** variant already stopped serving traffic on 2025-01-08.
- Google's recommended migration target is **Vertex AI Search** (good for ≤50 domains, not full web).

**Implication:** Even if the project's GCP account still has a working key, building a brand-new pipeline on CSE in 2026 is short-sighted — there is a hard ~9-month sunset clock and no guaranteed path for full-web search after that. This alone justifies switching now rather than in Phase 2.

## Architectural fit check (recap of our constraints)

The PRD's `core_non_negotiables` lock in:
- Backend controls the corpus passed to the LLM (no online model mode).
- LLM cites by `source_indices` only; backend resolves to canonical sources.
- Hard validator rejects out-of-window dates, unknown sources, hallucinated URLs.
- Soft allowlist + freshness ranking + canonical dedupe done in our code.

This means the search provider's job is narrow: **return a ranked list of candidate URLs with title/snippet/published-date**, optionally with extracted page text to skip our own fetch. It must NOT pre-synthesize an answer (that breaks the citation contract).

## Provider comparison

| Provider       | Returns                              | Approx. cost            | Latency  | Independent index? | Date filtering         | Fits our contract?                            |
|----------------|--------------------------------------|-------------------------|----------|--------------------|------------------------|-----------------------------------------------|
| Google CSE     | URL + snippet                        | $5/1k (10k/day cap)     | ~500ms   | Yes (Google)       | `dateRestrict` only    | Yes, but **deprecated**                       |
| **Tavily**     | URL + snippet + extracted content    | ~$8 CPM ($8/1k)         | ~1s      | Aggregator         | `days`/`start/end_date`| **Yes — best fit**                            |
| **Exa**        | URL + content + semantic ranking     | from $2.5 CPM           | ~1–2s    | Own neural index   | `start/end_published_date` | Yes — strong for discovery, semantic queries |
| Perplexity Sonar | LLM-synthesized answer + citations | $5/1k + token costs     | ~11s     | Aggregator         | Recency filter         | **No** — synthesizes its own answer, conflicts with our grounding contract |
| Brave Search   | URL + snippet                        | $5–9/1k                 | ~670ms   | Yes (own crawler)  | `freshness` (pd/pw)    | Yes — closest drop-in for CSE                 |

### Notes per provider

**Tavily** — Purpose-built for RAG. Single call returns search + extracted clean content, which would **let us drop trafilatura and the fetcher pipeline** in Phase 2 (`fetcher.py` shrinks to a thin fallback). Has explicit `include_domains`/`exclude_domains` (matches our soft allowlist), `days`/`start_date`/`end_date` (matches our prior-Mon–Fri window), and a `topic="news"` mode. Highest unit cost but lowest *engineering* cost for our shape.

**Exa** — Neural/semantic index. Strong for "find me articles about X concept" queries; weaker than Tavily for hard recency-keyed news queries unless you use their keyword mode. `/contents` endpoint returns full text. Cheapest at scale. Worth considering if we want to broaden beyond exact-keyword news matches (e.g., "Fed policy implications" type queries in the planner).

**Perplexity Sonar** — **Reject for this use case.** It does its own LLM synthesis with citations. Our PRD requires the backend to assemble the corpus and our `recap_generator` to do the writing, so Sonar would either be wasted work or directly violate `core_non_negotiables.grounding`. Useful only if we discarded the trust pipeline.

**Brave Search** — Independent index (not Bing/Google reseller), fast, cheap, simple JSON. Closest 1:1 swap for CSE. No content extraction → we keep trafilatura. Good fallback/secondary provider.

## Decision (confirmed with user)

**Tavily only** for Phase 2. Strict backend-controlled corpus stays as the grounding rule (Perplexity-style synthesis is off the table). Brave/Exa stay on the radar as future fallbacks but are not built in v1.

Rationale: Tavily collapses search + fetch + extraction into one call, has first-class news/date/domain filters that map directly to our query planner and source-policy layers, and removes most of the trafilatura failure surface from Phase 2's scope. Cost is acceptable at weekly cadence (one run × ~10–20 queries × ~1k cap = pennies/week). One provider keeps Phase 2 small.

### Phase 2 PRD edits implied by this choice

In [weekly-us-market-recap-prd.json](.claude/plans/weekly-us-market-recap-prd.json):
- `phase-2.scope_includes` — replace "Google CSE client and response normalization" with "Tavily search client and response normalization (single provider; abstraction designed to allow future fallbacks but not implemented in v1)".
- `phase-2.scope_includes` — soften the fetch/extract bullet: "Use Tavily-extracted content when present; fall back to httpx + trafilatura only for sources Tavily could not extract."
- `phase-2.files_expected` — `search_client.py` stays as the entry point but wraps `tavily_client.py`; `fetcher.py` stays but shrinks to fallback-only.
- `phase-2.files_expected` — `.env.example` keys: add `TAVILY_API_KEY`; drop `GOOGLE_CSE_*`.
- `phase-2.definition_of_done.implementation_checks` — add: "Search-client interface is provider-shaped (returns normalized candidate objects with title/url/snippet/published_date/extracted_content) so a second provider can be added later without touching ranking or source-policy code."

No changes needed to Phases 1, 3–8 — the citation/source_id contract from Phase 1 is provider-agnostic, which was the right call.

## Verification (when Phase 2 starts)

- Unit tests with recorded Tavily fixtures (no live calls — see `feedback_no_external_api_tests`).
- One manual dry-run for the prior-Mon–Fri window; confirm dedupe via `source_id_for` collapses near-duplicate hits and that `published_date` filtering excludes anything outside the window.
- Confirm Tavily `days`/`start_date`/`end_date` returns only in-window results before our own date-window filter runs; treat our filter as a belt-and-suspenders check, not load-bearing.

## Unresolved questions

1. Budget ceiling per weekly run? (Tavily ~$0.10–0.30/week is trivial; confirming nothing higher-tier is required.)
2. Should the soft allowlist live in our code (current PRD) or be pushed into Tavily's `include_domains` (cheaper, fewer wasted results, but couples policy to provider)?

Sources:
- [Custom Search JSON API — Google for Developers](https://developers.google.com/custom-search/v1/overview)
- [Custom Search JSON API closed to new customers (2026)](https://blog.expertrec.com/google-custom-search-json-api-simplified/)
- [Custom Search Site Restricted JSON API transitioning to Vertex AI Search](https://programmablesearchengine.googleblog.com/2023/12/custom-search-site-restricted-json-api.html)
- [Agentic Search in 2026: Benchmark 8 Search APIs for Agents](https://aimultiple.com/agentic-search)
- [Beyond Tavily — Complete Guide to AI Search APIs in 2026](https://websearchapi.ai/blog/tavily-alternatives)
- [Brave Search API — what sets it apart](https://brave.com/search/api/guides/what-sets-brave-search-api-apart/)
