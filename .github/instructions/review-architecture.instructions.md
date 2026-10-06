---
applyTo: "**"
excludeAgent: "cloud-agent"
---

# Code review: Architecture

This checklist covers the architecture angle of code review for the Stonkie backend.

Conventions are in `CLAUDE.md`. When reviewing a pull request, check the diff against them:

- 3-layer architecture: presentation (`api/`, routers) → `services/` → `connectors/`.
  - ALL I/O (3rd-party APIs such as Brave, yfinance, OpenRouter AND the database) lives in `connectors/`.
  - Per-entity `connectors/<entity>.py` with a `<Entity>Connector` class owning `SessionLocal` + ORM model; read/write methods (`get_*`, `upsert`, `delete_*`) return DTOs (frozen dataclasses). No ORM rows or `Session` objects escape a connector.
  - Services never `import SessionLocal`, never use raw SQLAlchemy (`select`, `insert`, `db.query`, `text`). Connectors are injected (`x or XConnector()`) so tests can pass fakes.
  - Routers (`api/`) call service methods only. A router must not import, construct or pass connectors or I/O clients: no `XConnector` / `YFinanceClient` / `BraveClient` imports, no `XConnector()` calls, no FastAPI `Depends` provider that returns a connector or client, and no handing a connector/client into a service function. Services own their collaborators (`x or XConnector()`); the router depends on a service provider (e.g. `Depends(get_portfolio_service)`) and calls its methods. The only names a router may import from `connectors.*` are DTO types (`*Dto`) for annotations. Flag every violation in the diff, including routers that pass `portfolio=`/`yf_client=`-style arguments to services. Canonical example: `api/portfolio.py` + `services/portfolio_service.py`. Known outliers (`api/quotes.py`, `api/tickers.py`, `api/deps.py`, `api/markets.py`) are allowlisted in `tests/architecture/test_router_layering.py`; flag new code that copies them, and flag any addition to that allowlist.
  - Canonical examples: `connectors/etf_fundamental.py`, `services/recap_analyze.py`. The `market_recap` service-layer `persistence.py` is a known outlier — flag new code that copies it.
- No inline (function-level) imports; circular deps solved by extracting shared modules.
- DB sessions only via `with SessionLocal() as db:` context managers.
- New modules in the right package; no god-files; no duplicated logic that already exists (search for existing helpers before flagging).
- Alembic migrations: one logical change, reversible `downgrade`, no destructive ops (drop table/column) without explicit intent.
- `tests/architecture/test_v2_layering.py` encodes layering rules — flag changes that would violate or weaken it.

Out of scope for this checklist (covered by the other review instructions): logic bugs, security, performance, test coverage.
