# CLAUDE.md — Stonkie Backend

See root `../CLAUDE.md` for shared conventions.

## Critical: Virtual Environment

**ALWAYS activate venv before ANY Python command:** `source venv/bin/activate`

**Scripts require PYTHONPATH:** `PYTHONPATH=. python scripts/script_name.py`

## Architecture: 3-layer (connector → service → model)

**All I/O lives in `connectors/`** — both 3rd-party APIs (Brave, yfinance) AND the database. A connector owns its sessions/SDK and exposes a repository: per-entity `connectors/<entity>.py` with a `<Entity>Connector` class holding `SessionLocal` + the ORM model, read+write methods (`get_*`, `upsert`, `delete_*`), returning **DTOs** (frozen dataclasses). No ORM rows or `Session` objects escape the connector.

**Services import/inject connectors and consume DTOs** — never `import SessionLocal`, never write raw SQLAlchemy (`insert`/`select`/`db.query`) in `services/`. Inject the connector as a param/ctor arg (`x or XConnector()`) so tests pass a fake.

- Canonical repository: `connectors/etf_fundamental.py`. Canonical consumer: `services/recap_analyze.py` (injects `MarketRecapConnector`, uses `MarketRecapDto`).
- **Outlier — do NOT copy:** `market_recap` writes via a service-layer `persistence.py` with an injected `db: Session`. That violates this rule; the connector pattern (e.g. `connectors/ticker_recap.py`) is correct.

## Gotchas

### `agent.generate_content()` returns mixed types
`MultiAgent.generate_content()` / `OpenRouterClient.stream_chat()` yields `Union[str, dict]` — text chunks AND url_citation annotation dicts (when `:online` model used). Code iterating over it MUST either:
- Pass through `_process_source_tags()` (preferred — extracts text, collects citations)
- Guard with `if not isinstance(chunk, str): continue` (drops citations)

### Celery memory management
Workers use `worker_max_tasks_per_child=1` (restart after each task) — required for Playwright memory cleanup. Don't change without understanding implications.

### Database sessions
Always use context managers: `with SessionLocal() as db:` — never manually manage session lifecycle.
