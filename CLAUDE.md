# CLAUDE.md — Stonkie Backend

See root `../CLAUDE.md` for shared conventions.

## Critical: Virtual Environment

**ALWAYS activate venv before ANY Python command:** `source venv/bin/activate`

**Scripts require PYTHONPATH:** `PYTHONPATH=. python scripts/script_name.py`

## Gotchas

### `agent.generate_content()` returns mixed types
`MultiAgent.generate_content()` / `OpenRouterClient.stream_chat()` yields `Union[str, dict]` — text chunks AND url_citation annotation dicts (when `:online` model used). Code iterating over it MUST either:
- Pass through `_process_source_tags()` (preferred — extracts text, collects citations)
- Guard with `if not isinstance(chunk, str): continue` (drops citations)

### Celery memory management
Workers use `worker_max_tasks_per_child=1` (restart after each task) — required for Playwright memory cleanup. Don't change without understanding implications.

### Database sessions
Always use context managers: `with SessionLocal() as db:` — never manually manage session lifecycle.
