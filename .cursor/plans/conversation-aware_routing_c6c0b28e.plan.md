---
name: Conversation-aware routing
overview: Improve question classification so vague follow-ups reuse prior conversation context (and don’t treat ticker='undefined' as a real company), preventing misroutes to company-specific-finance when the user expects an immediate contextual answer.
todos:
  - id: normalize-ticker
    content: Treat ticker values like 'undefined'/'null'/'' as no-ticker context for classification and routing.
    status: completed
  - id: classifier-with-context
    content: Extend QuestionClassifier to accept conversation_messages and incorporate last turns into the classification prompt.
    status: completed
  - id: sticky-routing-meta
    content: Store last QuestionType in Redis conversation meta; reuse it for ambiguous follow-ups to avoid misrouting.
    status: completed
  - id: company-specific-fallback
    content: "Add safety fallback: if routed to company-specific-finance but ticker/data missing, answer generally instead of refusing."
    status: completed
  - id: unit-tests-routing
    content: Add minimal unit tests for ticker normalization, sticky routing, and conversation-aware classification prompt construction (mock LLM).
    status: pending
---

# Conversation-aware classification & fallback

## Goal

Ensure vague follow-up questions (e.g., “Which are potential areas to reinvest?”) are **classified based on the existing conversation context**, so they don’t incorrectly route to `company-specific-finance` and trigger heavy data fetching / “No Data Available”.

## Root cause (current)

- `conversation_messages` are injected into the *answer prompts*, **but not used by** `QuestionClassifier.classify_question_type`.
- The classifier prompt also treats any provided ticker as real; if ticker is literally `"undefined"`, the rule:
- “When ticker is provided ({ticker}) … classify as company-specific-finance”
accidentally biases routing.

## Approach

### 1) Normalize ticker (`undefined` should mean “no ticker”)

- In [`main.py`](/Users/vinhle/dev/projects/stonkie/backend/main.py) and/or [`services/financial_analyzer.py`](/Users/vinhle/dev/projects/stonkie/backend/services/financial_analyzer.py):
- Normalize `ticker` such that `None`, `""`, `"undefined"`, `"null"` are treated as **no ticker context** for classification.

### 2) Make `QuestionClassifier` conversation-aware (prompt-only)

- Extend [`services/question_analyzer/classifier.py`](/Users/vinhle/dev/projects/stonkie/backend/services/question_analyzer/classifier.py):
- Update `classify_question_type(question, ticker, conversation_messages=None)`.
- Include a short conversation snippet (last 1–3 turns) into the classifier prompt:
- If the new question is ambiguous, instruct the model to treat it as a follow-up to the conversation topic.
- If ticker is missing, remove/soften rules that force “company-specific-finance”.

### 3) Add “sticky routing” for ambiguous follow-ups (no extra LLM call)

- Add conversation metadata in Redis (separate key) to store the last chosen `QuestionType` for the conversation.
- New module or extend [`connectors/conversation_store.py`](/Users/vinhle/dev/projects/stonkie/backend/connectors/conversation_store.py) with:
- `get_conversation_meta(...)` / `set_conversation_meta(...)`
- store fields like `{ "last_question_type": "general-finance" | "company-general" | "company-specific-finance" }`
- In [`services/financial_analyzer.py`](/Users/vinhle/dev/projects/stonkie/backend/services/financial_analyzer.py):
- If `conversation_messages` exist and the new question matches an “ambiguous follow-up” heuristic (short, no entity, no explicit metrics), reuse `last_question_type` instead of re-classifying.
- Otherwise, run the classifier (now with conversation context).
- After selecting a classification, persist it back into meta.

### 4) Fallback behavior when misrouted to company-specific-finance

- In [`services/question_analyzer/company_specific_finance_handler.py`](/Users/vinhle/dev/projects/stonkie/backend/services/question_analyzer/company_specific_finance_handler.py):
- If `ticker` is missing/undefined OR `company_fundamental` and statements are empty, yield a concise helpful answer (strategy-level guidance) instead of refusing.
- This is a safety net; the primary fix is classification.

### 5) Unit tests (minimal, no LLM/token burn)

- Update/add tests under `tests/`:
- Ticker normalization: `"undefined"` does not force `company-specific-finance`.
- Sticky routing: ambiguous follow-up reuses previous `QuestionType` from meta.
- Conversation-aware classifier prompt assembly can be smoke-tested by mocking `MultiAgent.generate_content`.

## Notes on the screenshot scenario

- If the prior question was `general-finance` (cash flow concept) and the follow-up is short/ambiguous (“areas to reinvest?”), sticky routing should keep it `general-finance` and answer immediately.

## Files to change

- [`main.py`](/Users/vinhle/dev/projects/stonkie/backend/main.py)
- [`services/financial_analyzer.py`](/Users/vinhle/dev/projects/stonkie/backend/services/financial_analyzer.py)
- [`services/question_analyzer/classifier.py`](/Users/vinhle/dev/projects/stonkie/backend/services/question_analyzer/classifier.py)
- [`connectors/conversation_store.py`](/Users/vinhle/dev/projects/stonkie/backend/connectors/conversation_store.py)
- [`services/question_analyzer/company_specific_finance_handler.py`](/Users/vinhle/dev/projects/stonkie/backend/services/question_analyzer/company_specific_finance_handler.py)
- `tests/` (new/updated unit tests)