# Code review: Security

This checklist covers the security angle of code review for the Stonkie backend (FastAPI, PostgreSQL, Google login with users table + `GET /api/me`, LLM prompts built from user questions).

When reviewing a pull request, check:
- AuthN/AuthZ: new endpoints touching user data (e.g. portfolio, `/api/me`) must require auth and scope queries to the authenticated user (IDOR). Compare with how existing authed routes get the current user.
- Injection: SQL built by string formatting / f-strings / `text()` with interpolated input; shell commands with user input; path traversal in file paths.
- Prompt injection: user- or web-sourced text inserted into LLM system prompts without delimiting; model output trusted to drive tool calls or DB writes.
- Secrets: API keys, tokens, DB URLs committed in code, logged, or returned in responses/errors.
- Input validation: missing Pydantic validation/limits on query/body params (ticker format, pagination limits, string lengths).
- Data exposure: responses including internal fields, other users' data, stack traces.
- CORS / cookie / token settings loosened; OAuth token verification skipped or audience not checked.
- SSRF: fetching user-supplied URLs without allowlisting.
- Dependencies: newly added packages that are unpinned or unmaintained.

Out of scope for this checklist (covered by the other review instructions): logic bugs unrelated to security, layering, performance, test coverage.
