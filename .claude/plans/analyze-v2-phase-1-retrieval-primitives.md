# analyze-v2 phase-1 — retrieval primitives (TDD strict)

PRD: `.claude/plans/analyze-v2-brave-migration-prd.json` (phase-1-retrieval-primitives).

## Context

Phase-0 (source policy) is complete. Phase-1 ships the pure-function building blocks the orchestrator (phase-3) depends on: market resolution, goggle string, publisher labels, schemas. No I/O. No Brave calls. No fastapi imports (Layer-2 services).

User decisions (this session):
- `*.gov` is NOT emitted in goggle output. Rely on rank-time `tier_for()`. (`$boost=4,site=*.gov` is not standard Brave goggle syntax.)
- Country comes from `company_fundamental.data['country']` (already fetched by current `/analyze`). Phase-1 just normalizes the string. Q3.1 (DB distinct query) deferred — map known values, default to GLOBAL.
- VN tier-2 publisher labels accepted as proposed for phase-1.
- Market resolution uses direct country mapping only (no question-text heuristic): `USA|United States -> GLOBAL`, `Vietnam -> VN`, `Finland -> FI`, else `GLOBAL`.
- `AnalyzeSource.id` generation helper is deferred to phase-2; phase-1 only defines schema/interface.
- `AnalyzeSource.published_at` remains optional (`datetime | None`); ranking treats missing date as old/no recency boost.

## Files

New (Layer 2 — `services/`):
- `services/analyze_retrieval/__init__.py` (re-exports)
- `services/analyze_retrieval/schemas.py`
- `services/analyze_retrieval/market.py`
- `services/analyze_retrieval/goggle.py`
- `services/analyze_retrieval/publisher.py`

New tests:
- `tests/services/analyze_retrieval/test_schemas.py`
- `tests/services/analyze_retrieval/test_market.py`
- `tests/services/analyze_retrieval/test_goggle.py`
- `tests/services/analyze_retrieval/test_publisher.py`

Reuses (already on disk):
- `services/analyze_retrieval/source_policy.py` — `Market`, `GLOBAL_TIER_1/2`, `FI_EXTENSION_TIER_1/2`, `VN_TIER_1/2`, `DISCARDS`, `tier_for`, `is_trusted`, `is_discarded`, `registrable_domain`.

Reference for style only (do NOT import — analyze_v2 owns its own):
- `services/market_recap/schemas.py` (Pydantic Candidate/Source style)
- `services/market_recap/brave_client.py::_build_goggle` (goggle directive shape)

## Order of work (strict TDD: red → green per module)

1. **schemas first** (other modules don't depend on it for now, but it anchors types).
   - Write `test_schemas.py` → run → **RED** (ImportError).
   - Implement `schemas.py` → run → **GREEN**.

2. **market.py**
   - Write `test_market.py` → **RED**.
   - Implement `market.py` → **GREEN**.

3. **goggle.py**
   - Write `test_goggle.py` → **RED**.
   - Implement `goggle.py` → **GREEN**.

4. **publisher.py**
   - Write `test_publisher.py` → **RED**.
   - Implement `publisher.py` → **GREEN**.

5. **`__init__.py`** — add re-exports: `resolve_market`, `build_chat_goggle`, `publisher_label_for`, `AnalyzeSource`, `AnalyzeRetrievalResult`, `BraveRetrievalError`, `Market`.

6. **Baseline checks** (mandatory hard gate per PRD):
   - `source venv/bin/activate && PYTHONPATH=. pytest tests/services/analyze_retrieval/ -v`
   - `source venv/bin/activate && pytest tests/test_healthcheck.py -v`
   - `source venv/bin/activate && ruff check .`

7. **Update PRD** phase-1 entry: `state=complete`, `validation_summary`, `tdd_evidence` (red+green logs), `gates_result_snapshot`, `learnings`, `next_phase_considerations`.

8. **Stop**. Wait for explicit user approval before phase-2 (per `phase_transition_policy.manual_approval_required`).

## Module sketches

### `schemas.py` (Pydantic, mirrors `market_recap/schemas.py`)
```python
from datetime import datetime
from pydantic import BaseModel
from services.analyze_retrieval.source_policy import Market

class AnalyzeSource(BaseModel):
    id: str
    url: str
    title: str
    publisher: str
    published_at: datetime | None = None
    is_trusted: bool

class AnalyzeRetrievalResult(BaseModel):
    sources: list[AnalyzeSource]
    query: str
    market: Market
    request_id: str

class BraveRetrievalError(Exception): ...
```

### `market.py`
```python
from services.analyze_retrieval.source_policy import Market

_COUNTRY_TO_MARKET: dict[str, Market] = {
    "us": "GLOBAL", "usa": "GLOBAL",
    "united states": "GLOBAL", "united states of america": "GLOBAL",
    "vn": "VN", "vietnam": "VN", "viet nam": "VN",
    "fi": "FI", "finland": "FI",
}
def resolve_market(country: str | None, question_text: str) -> Market:
    _ = question_text
    if country:
        key = country.strip().lower()
        if key in _COUNTRY_TO_MARKET:
            return _COUNTRY_TO_MARKET[key]
    return "GLOBAL"
```

### `goggle.py`
```python
from services.analyze_retrieval.source_policy import (
    Market, GLOBAL_TIER_1, GLOBAL_TIER_2,
    FI_EXTENSION_TIER_1, FI_EXTENSION_TIER_2,
    VN_TIER_1, VN_TIER_2, DISCARDS,
)

def build_chat_goggle(market: Market) -> str:
    if market == "VN":
        t1, t2 = set(VN_TIER_1), set(VN_TIER_2)
    elif market == "FI":
        t1 = set(GLOBAL_TIER_1) | set(FI_EXTENSION_TIER_1)
        t2 = set(GLOBAL_TIER_2) | set(FI_EXTENSION_TIER_2)
    else:  # GLOBAL
        t1, t2 = set(GLOBAL_TIER_1), set(GLOBAL_TIER_2)

    # Skip wildcard entries (e.g. "*.gov") — invalid Brave goggle syntax;
    # ranking still applies tier-1 boost via tier_for() at rank time.
    t1_clean = sorted(d for d in t1 if not d.startswith("*."))
    t2_clean = sorted(d for d in t2 if d not in t1)
    lines  = [f"$boost=4,site={d}" for d in t1_clean]
    lines += [f"$boost=2,site={d}" for d in t2_clean]
    lines += [f"$discard={d}" for d in sorted(DISCARDS)]
    return "\n".join(lines)
```
Path-prefix discards (`DISCARD_PATH_PREFIXES`, e.g. `tradingview.com/ideas`) are NOT emitted; rank-time `is_discarded()` covers them.

### `publisher.py`
```python
from urllib.parse import urlparse
from services.analyze_retrieval.source_policy import registrable_domain

_PUBLISHER_LABELS: dict[str, str] = {
    # GLOBAL T1
    "reuters.com": "Reuters", "bloomberg.com": "Bloomberg",
    "ft.com": "Financial Times", "wsj.com": "Wall Street Journal",
    "cnbc.com": "CNBC", "barrons.com": "Barron's",
    "economist.com": "The Economist", "marketwatch.com": "MarketWatch",
    "sec.gov": "U.S. SEC", "companieshouse.gov.uk": "Companies House",
    "sedarplus.ca": "SEDAR+", "europa.eu": "European Union",
    # GLOBAL T2
    "investing.com": "Investing.com",
    "finance.yahoo.com": "Yahoo Finance",  # full-host key
    "morningstar.com": "Morningstar", "stockanalysis.com": "StockAnalysis",
    "macrotrends.net": "Macrotrends", "simplywall.st": "Simply Wall St",
    "seekingalpha.com": "Seeking Alpha",
    "ishares.com": "iShares", "vanguard.com": "Vanguard",
    "ssga.com": "State Street Global Advisors", "invesco.com": "Invesco",
    "blackrock.com": "BlackRock", "schwab.com": "Charles Schwab",
    "fidelity.com": "Fidelity",
    "am.jpmorgan.com": "J.P. Morgan Asset Management",
    "statestreet.com": "State Street",
    # FI
    "inderes.fi": "Inderes", "kauppalehti.fi": "Kauppalehti",
    "hs.fi": "Helsingin Sanomat", "yle.fi": "Yle", "arvopaperi.fi": "Arvopaperi",
    # VN T1
    "hsx.vn": "HOSE", "hnx.vn": "HNX", "ssc.gov.vn": "Vietnam SSC",
    "cafef.vn": "CafeF", "vneconomy.vn": "VnEconomy",
    "vietstock.vn": "Vietstock", "vir.com.vn": "Vietnam Investment Review",
    # VN T2 — minimal curated set; rest fall back to titlecase
    "tinnhanhchungkhoan.vn": "Tin Nhanh Chứng Khoán",
    "ssi.com.vn": "SSI", "vndirect.com.vn": "VNDirect",
    "vcsc.com.vn": "VCSC", "hsc.com.vn": "HSC",
    "bsc.com.vn": "BSC", "mbs.com.vn": "MBS", "fpts.com.vn": "FPTS",
}

def publisher_label_for(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().lstrip("www.")
    if not host:
        return ""
    # full hostname first (e.g. finance.yahoo.com), then registrable domain.
    if host in _PUBLISHER_LABELS:
        return _PUBLISHER_LABELS[host]
    dom = registrable_domain(url)
    if dom and dom in _PUBLISHER_LABELS:
        return _PUBLISHER_LABELS[dom]
    if not dom:
        return ""
    base = dom.rsplit(".", 1)[0]
    return base.replace("-", " ").title()
```

## Test cases

### `test_schemas.py`
- `AnalyzeSource(id, url, title, publisher, is_trusted=True)` constructs; `published_at` defaults to `None`.
- Missing required field → `ValidationError`.
- `AnalyzeRetrievalResult(sources=[], query="q", market="GLOBAL", request_id="r")` ok; `market="XX"` → `ValidationError`.
- `issubclass(BraveRetrievalError, Exception)`.

### `test_market.py`
- `("USA", "")` → `"GLOBAL"`; `("United States", "")` → `"GLOBAL"`.
- `("Vietnam", "")` → `"VN"`; `("VN", "")` → `"VN"`; `(" vietnam ", "")` → `"VN"`.
- `("Finland", "")` → `"FI"`; `("FI", "")` → `"FI"`.
- `("Atlantis", "")` → `"GLOBAL"` (unknown country).
- `(None, "")` → `"GLOBAL"`; `("", "")` → `"GLOBAL"`.
- `(None, "Cổ phiếu Vinamilk tăng mạnh")` → `"GLOBAL"` (missing country defaults to GLOBAL).
- `("", "Tình hình thị trường")` → `"GLOBAL"` (empty country defaults to GLOBAL).
- `("USA", "Cổ phiếu …")` → `"GLOBAL"` (country wins).
- `(None, "Talvivaara louhintapäivät")` → `"GLOBAL"` (question text is ignored for market detection).

### `test_goggle.py`
- For each market in `["GLOBAL", "VN", "FI"]`:
  - Output contains `$boost=4,site=<d>` for every non-wildcard tier-1 domain.
  - Output contains `$boost=2,site=<d>` for every tier-2 domain.
  - Every `DISCARDS` entry appears as `$discard=<d>`.
- `*.gov` substring NOT present in any market's output.
- No tier-1 domain appears as a `$boost=2` line (assert disjoint).
- VN: `reuters.com` NOT present (no GLOBAL inheritance).
- FI: `reuters.com` AT `boost=4` AND `inderes.fi` AT `boost=4`; `hs.fi` AT `boost=2`.
- Determinism: `build_chat_goggle("GLOBAL") == build_chat_goggle("GLOBAL")` (sorted output).
- `tradingview.com/ideas` NOT in output (path-prefix discards are skipped).

### `test_publisher.py`
- `https://www.reuters.com/article/x` → `"Reuters"` (www stripped).
- `https://reuters.com/` → `"Reuters"`.
- `https://finance.yahoo.com/quote/AAPL` → `"Yahoo Finance"` (full-host key wins).
- `https://cafef.vn/foo` → `"CafeF"`.
- `https://example-site.com/` → `"Example Site"` (titlecase + dash→space fallback).
- `"not a url"` → `""`.
- `https://reuters.com/?utm_source=x` → `"Reuters"` (query ignored).

## Verification (run after step 5)

```
source venv/bin/activate && PYTHONPATH=. pytest tests/services/analyze_retrieval/ -v
source venv/bin/activate && pytest tests/test_healthcheck.py -v
source venv/bin/activate && ruff check .
```

Phase gates (per PRD):
- A: TDD red→green proof for each of 4 modules (capture both runs).
- B: all phase tests pass.
- C: healthcheck + ruff baseline pass.

## Unresolved questions (concise)

1. None for phase-1 scope.
