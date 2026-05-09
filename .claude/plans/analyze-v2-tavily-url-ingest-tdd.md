# Analyze v2 Tavily URL Ingest, TDD Plan

## Summary
Build explicit URL/document grounding for analyze v2 using Tavily Extract as the primary provider. This covers DB-stored 10-Q/10-K filing URLs and user-pasted `useUrlContext=true` URLs through one path. For this first pass, use Tavily's query-focused chunks only; no full-document fallback or local deep reranking yet.

## Key Changes
- Add a v2 URL ingest layer: a thin Tavily Extract HTTP client using `TAVILY_API_KEY`, plus an L2 service that converts Tavily `raw_content` chunks into existing v2 source/passage objects.
- Use Tavily Extract with `query=<user question>`, `chunks_per_source=5`, `format="markdown"`; filing/report URLs use `extract_depth="advanced"` and `timeout=30`, user URLs use `extract_depth="basic"` and `timeout=10`.
- Route DB-stored `filing_10q_url` / `filing_10k_url` and explicit `useUrlContext=true` URLs through the same ingest path; v1 stays unchanged.
- Keep `attachment_url`, reuse final `{type:"sources"}`, preserve raw inline `[N]` answer markers, and fail visibly with `url_ingest_failed` when extraction fails.
- Add structured logs for provider, query, requested/successful/failed URLs, chunk count, selected source IDs, source metadata, response time, Tavily request id, and usage. Do not log full snippets or raw content.

## TDD Plan
1. 10-Q happy path: quarterly-summary request with stored `filing_10q_url` emits `attachment_url`, Tavily chunks in prompt, answer, `sources`, and safe structured ingest logs.
2. 10-K parity: annual-summary request with `filing_10k_url` follows the same path.
3. User URL mode: `useUrlContext=true` with pasted URL ingests Tavily chunks and skips Brave.
4. Failure behavior: failed/empty extraction emits `url_ingest_failed`, logs failed URL metadata, and skips LLM.
5. Cache guard: URL-grounded requests are not semantic-cache eligible.

## Validation
- Mock Tavily in automated tests; no live Tavily, Brave, or OpenRouter calls.
- Run targeted tests, existing v2 handler/endpoint tests, `pytest tests/test_healthcheck.py -v`, and `ruff check .`.
- Final live validation: start backend on `localhost:8080`, curl two v2 report questions, verify `attachment_url`, coherent answer, final `sources`, and safe Tavily ingest logs for both.
