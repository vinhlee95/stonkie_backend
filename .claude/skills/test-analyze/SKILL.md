---
name: test-analyze
description: Manually test the /analyze endpoint by POSTing questions and inspecting SSE responses
when_to_use:
  - After modifying search decision logic, classifiers, or analysis handlers
  - After changing prompt templates or model routing
  - When the user asks to manually test a question against the analyze endpoint
  - When requested by user with /test-analyze
---

# Manual Test: Analyze Endpoint

POST questions to the `/api/companies/{ticker}/analyze` SSE endpoint and inspect results.

## Usage

The user may provide:
- A specific question and ticker to test
- A list of questions to test in batch
- A specific field to check (e.g., `search_decision`, `model_used`, answer content)

If not specified, ask the user what question(s) and ticker(s) to test.

## How to test

Server runs on `localhost:8080`. Use curl with `-s -N` for SSE streaming:

```bash
curl -s -N -X POST http://localhost:8080/api/companies/{TICKER}/analyze \
  -H "Content-Type: application/json" \
  -d '{"question": "THE QUESTION"}' 2>&1
```

### Common fields to inspect

Filter specific SSE event types from the stream:

- **Search decision**: `| grep "search_decision_meta"` — shows `search_decision` (on/off), `reason_code`, `decision_model`, `confidence`
- **Model used**: `| grep "model_used"` — which LLM generated the answer
- **Answer content**: `| grep '"type": "answer"'` — streamed answer chunks
- **Sources**: `| grep "sources"` — citation URLs
- **Full response**: pipe to `head -N` to limit output

### Request body options

```json
{
  "question": "string (required)",
  "useUrlContext": false,
  "deepAnalysis": false,
  "preferredModel": "auto",
  "conversationId": "optional-uuid",
  "conversationMessages": []
}
```

### ETF tickers

ETF tickers (e.g., SPY, QQQ, VOO) route to the ETF analyzer automatically.

## Reporting results

Show the user the relevant SSE fields for each test. If testing multiple questions, summarize results in a table format:

| Question | Ticker | Search Decision | Reason Code | Model |
|----------|--------|----------------|-------------|-------|
| ...      | ...    | on/off         | ...         | ...   |
