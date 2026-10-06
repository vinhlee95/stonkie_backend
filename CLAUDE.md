# CLAUDE.md — Stonkie Backend

See root `../CLAUDE.md` for shared conventions.

## Critical: Virtual Environment

**ALWAYS activate venv before ANY Python command:** `source venv/bin/activate`

**Scripts require PYTHONPATH:** `PYTHONPATH=. python scripts/script_name.py`

## Architecture: 3-layer (connector → service → model)

**All I/O lives in `connectors/`** — both 3rd-party APIs (Brave, yfinance) AND the database. A connector owns its sessions/SDK and exposes a repository: per-entity `connectors/<entity>.py` with a `<Entity>Connector` class holding `SessionLocal` + the ORM model, read+write methods (`get_*`, `upsert`, `delete_*`), returning **DTOs** (frozen dataclasses). No ORM rows or `Session` objects escape the connector.

**One router → one service, helpers private** (enforced by `tests/architecture/test_service_layering.py`; canonical: `api/portfolio.py` + `services/portfolio/`):
- `api/<feature>.py` imports only `from services.<feature> import <Feature>Service, …` (plus `*Dto` types). No connector/client imports, construction or `Depends` providers, no service submodule imports.
- `services/<feature>/service.py` holds the single `<Feature>Service`; it alone imports, constructs (`x or XConnector()`) and calls connectors/clients (incl. Redis `cache`, LLM agents, langfuse). Helper modules in the package are **pure functions**: they take DTOs/plain data, never a connector/client argument, and import only `*Dto` types from `connectors`. Helpers are private to the package. Grow by adding helpers in the package, never sibling `services/<feature>_*.py` files.
- No service-to-service calls: a feature package imports only itself, `services/shared/` and shared libs (`analyze_retrieval`, `analysis_progress`). Logic two features need goes to `services/shared/`.

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
