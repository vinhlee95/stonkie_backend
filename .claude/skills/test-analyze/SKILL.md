---
name: test-analyze
description: Manually test the /analyze endpoint by POSTing questions and inspecting SSE responses
when_to_use:
  - After modifying question routing, search decisions, retrieval, or analysis handlers
  - After changing prompt templates or model routing
  - When the user asks to manually test a question against the analyze endpoint
  - When requested by user with /test-analyze
---

# Manual Test: Analyze Endpoint

POST questions to the `/api/v2/companies/{ticker}/analyze` SSE endpoint and inspect results. Stocks and ETFs share
this endpoint; see `ANALYZE_ENDPOINT.md` for the flow.

## Usage

The user may provide:
- A specific question and ticker to test
- A list of questions to test in batch
- A specific field to check (e.g., `thinking_status`, `model_used`, answer content)

If not specified, ask the user what question(s) and ticker(s) to test.

## How to test

Server runs on `localhost:8080`. Use curl with `-s -N` for SSE streaming:

```bash
curl -s -N -X POST http://localhost:8080/api/v2/companies/{TICKER}/analyze \
  -H "Content-Type: application/json" \
  -d '{"question": "THE QUESTION", "disableCache": true}' 2>&1
```

Use `undefined` as `{TICKER}` for questions with no company selected. Each event is one JSON object followed by a
blank line (no `data:` prefix).

### Common fields to inspect

Filter specific SSE event types from the stream:

- **Routing / search path**: `| grep "thinking_status"` — always starts with "Understanding your question..." and
  ends with "Writing your answer..." (with "from N sources: <publishers>" when web sources were used); a
  `Searching for "<query>"...` step in between means search was on. The server log line
  `QuestionRouter <secs>: type=... data=... search=... reason=... queries=... standalone=...` (or
  `QuestionRouter ETF ...`) shows the full routing decision; `QuestionRouter failed ... falling back to default routing`
  means the router call failed.
- **Answer content**: `| grep '"type": "answer"'` — streamed answer chunks (HTML/visual blocks arrive as
  `answer_visual_start` / `answer_visual_delta` / `answer_visual_done`)
- **Sources**: `| grep '"type": "sources"'` — retrieved Brave sources (`url`, `title`, `publisher`, `is_trusted`)
- **Model used**: `| grep "model_used"` — which LLM generated the answer
- **Follow-ups**: `| grep "related_question"`
- **Errors**: `| grep '"type": "error"'` — `retrieval_failed` (Brave) or `url_ingest_failed`
- **Prompt debugging**: send `"debugPromptContext": true` and `| grep "debug_prompt_context"` to see the final prompt
  and the passages it was grounded on
- **Full response**: pipe to `head -N` to limit output

### Request body options

```json
{
  "question": "string (required)",
  "conversationId": "optional; reuse the id from the first `conversation` event to ask a follow-up",
  "useUrlContext": false,
  "preferredModel": "fastest",
  "disableCache": false,
  "debugPromptContext": false
}
```

Conversation history is loaded server-side from `conversationId` (scoped by the `anon_user_id` cookie), so send the
cookie back (`-c cookies.txt -b cookies.txt`) when testing follow-ups. `deepAnalysis` is ignored on v2.

### ETF tickers

ETF tickers that exist in the ETF table (e.g., SXR8, CSPX, VUSA) route to the ETF analyzer automatically.

## Reporting results

Show the user the relevant SSE fields for each test. If testing multiple questions, summarize results in a table format:

| Question | Ticker | Routing (type / search / queries) | Sources | Model |
|----------|--------|-----------------------------------|---------|-------|
| ...      | ...    | from the `QuestionRouter` log line | count  | ...   |
