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
  - One router → one service. `api/<feature>.py` imports exactly one service entry — the root of its feature package (`from services.<feature> import <Feature>Service, …errors`) — and only `*Dto` types from `connectors`. It never imports, constructs, `Depends`-injects or passes connectors/clients (`XConnector`, `YFinanceClient`, `BraveClient`, …), and never imports a service submodule (`services.<feature>.<helper>`). The router depends on a provider (`Depends(get_<feature>_service)`) and calls one service method per endpoint.
  - Feature package = `services/<feature>/` with a `service.py` defining exactly one `<Feature>Service`. That class is the only entry point and the only place connectors/clients are constructed (`x or XConnector()`); it passes them into helper functions as arguments. Every other module in the package is a private helper: never imported from outside the package, never constructing connectors, and not a second service class. When `service.py` grows, move logic into helper modules in the same package — do not create sibling service files (`services/<feature>_<thing>.py`).
  - No service-to-service calls. Feature package code may import its own package, `services/shared/` and the listed shared libraries (`services/analyze_retrieval`, `services/analysis_progress`), plus `connectors`, `utils`, `ai_models`, `agent` — never another feature's service or helpers. Logic two features need moves to `services/shared/` as plain helpers (no service classes there).
  - Flag every violation in the diff: routers passing connectors/clients (`portfolio=`, `yf_client=`) into services, a service class instantiating another service class, helpers instantiating connectors, sibling `*_service.py`/`<feature>_<thing>.py` files next to a feature package, or additions to the allowlists. Enforced by `tests/architecture/test_service_layering.py`; `KNOWN_ROUTER_OUTLIERS` (analyze_v2, companies, deps, markets, quotes, recap_analyze, tickers) predates the rule — flag new code that copies those routers. Canonical example: `api/portfolio.py` + `services/portfolio/`.
  - Canonical examples: `connectors/etf_fundamental.py`, `services/recap_analyze.py`. The `market_recap` service-layer `persistence.py` is a known outlier — flag new code that copies it.
- No inline (function-level) imports; circular deps solved by extracting shared modules.
- DB sessions only via `with SessionLocal() as db:` context managers.
- New modules in the right package; no god-files; no duplicated logic that already exists (search for existing helpers before flagging).
- Alembic migrations: one logical change, reversible `downgrade`, no destructive ops (drop table/column) without explicit intent.
- `tests/architecture/test_v2_layering.py` encodes layering rules — flag changes that would violate or weaken it.

Out of scope for this checklist (covered by the other review instructions): logic bugs, security, performance, test coverage.
