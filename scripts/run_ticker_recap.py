"""Run the daily per-ticker news recap for a fixed set of popular US tickers.

Production: runs as Cloud Run job `daily-ticker-recap` (europe-north1), triggered
Tue-Sat 05:00 Helsinki by Cloud Scheduler. Exit code 0 = at least one ticker
succeeded; exit code 1 = every ticker failed/skipped (triggers alert).
See docs/gcp_cronjobs.md for deploy/update/rollback commands.

Adding tickers without a redeploy: set the RECAP_TICKERS env var on the Cloud
Run job. It merges into (and overrides) the built-in POPULAR_TICKERS set, so you
only list the *new* tickers. Format is semicolon-separated records of
`TICKER:Company Name:MARKET` (market optional, defaults to US); names may contain
commas/spaces but not ':' or ';'. Example:
    RECAP_TICKERS="NKE:NIKE, Inc.;DELL:Dell Technologies Inc."

Every ticker a user holds is recapped too, when its exchange is US/FI/VN and Yahoo lists it as an equity
(no ETFs/funds/crypto/indices), named by Yahoo. They run after the popular and RECAP_TICKERS set, most-held
first, up to RECAP_MAX_TICKERS (default 20) in total, so the cap drops the least-held. Tickers already
recapped for the period are skipped before any paid call, so a rerun resumes where a timed-out run stopped.

Local usage:
    source venv/bin/activate
    PYTHONPATH=. python scripts/run_ticker_recap.py --cadence daily
    PYTHONPATH=. python scripts/run_ticker_recap.py --tickers NVDA,AAPL
    PYTHONPATH=. python scripts/run_ticker_recap.py --ticker AAPL \
        --period-start 2026-06-26 --period-end 2026-06-26 --replace
"""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import date, datetime

from connectors.portfolio import PortfolioConnector
from connectors.yfinance_client import TickerInfoDto, YFinanceClient
from scripts.run_market_recap import (
    MARKET_TZ,
    NY_TZ,
    compute_latest_completed_trading_day,
)
from services.market_recap.logging import log_event
from services.ticker_recap.orchestrator import run_ticker_recap
from services.ticker_recap.universe import (
    DEFAULT_MAX_TICKERS,
    HeldTicker,
    build_universe,
    market_for_ticker,
)

logger = logging.getLogger(__name__)

# Built-in popular US tickers; {ticker: {name, market}} map drives the company
# name in prompts and (future) per-market routing. US-only for now. Extend at
# runtime (no redeploy) via the RECAP_TICKERS env var — see module docstring.
POPULAR_TICKERS: dict[str, dict[str, str]] = {
    "NVDA": {"name": "NVIDIA Corporation", "market": "US"},
    "AAPL": {"name": "Apple Inc.", "market": "US"},
    "TSLA": {"name": "Tesla, Inc.", "market": "US"},
    "GOOG": {"name": "Alphabet Inc.", "market": "US"},
}

RECAP_TICKERS_ENV = "RECAP_TICKERS"
RECAP_MAX_TICKERS_ENV = "RECAP_MAX_TICKERS"
EVENT_UNIVERSE = "ticker_recap.universe"
LOOKUP_TIMEOUT_S = 15


def _parse_ticker_env(raw: str | None) -> dict[str, dict[str, str]]:
    """Parse RECAP_TICKERS into a {ticker: {name, market}} map.

    Semicolon-separated records, colon-separated fields (`TICKER:Name:MARKET`).
    Market is optional (defaults to US); a missing/empty name falls back to the
    ticker. Malformed/empty records are skipped rather than aborting the run.
    """
    catalog: dict[str, dict[str, str]] = {}
    if not raw:
        return catalog
    for record in raw.split(";"):
        fields = [field.strip() for field in record.split(":")]
        ticker = fields[0].upper()
        if not ticker:
            continue
        name = fields[1] if len(fields) > 1 and fields[1] else ticker
        market = fields[2].upper() if len(fields) > 2 and fields[2] else "US"
        catalog[ticker] = {"name": name, "market": market}
    return catalog


def _ticker_catalog(env: dict[str, str]) -> dict[str, dict[str, str]]:
    """Built-in tickers merged with (and overridden by) RECAP_TICKERS entries."""
    return {**POPULAR_TICKERS, **_parse_ticker_env(env.get(RECAP_TICKERS_ENV))}


def _max_tickers(env: dict[str, str]) -> int:
    raw = env.get(RECAP_MAX_TICKERS_ENV)
    try:
        return int(raw) if raw else DEFAULT_MAX_TICKERS
    except ValueError:
        logger.warning("ticker_recap: invalid %s=%r; using %d", RECAP_MAX_TICKERS_ENV, raw, DEFAULT_MAX_TICKERS)
        return DEFAULT_MAX_TICKERS


def _default_held() -> list[HeldTicker]:
    return PortfolioConnector().held_ticker_counts()


def _with_timeout(
    lookup: Callable[[str], TickerInfoDto | None], timeout_s: float
) -> Callable[[str], TickerInfoDto | None]:
    """Gives up on a Yahoo lookup after `timeout_s` (the ticker is then tried under its own name) so one hung
    request can't eat the job's time budget. The abandoned call finishes on a pool thread in the background."""
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="recap-lookup")

    def call(ticker: str) -> TickerInfoDto | None:
        future = pool.submit(lookup, ticker)
        try:
            return future.result(timeout=timeout_s)
        except FutureTimeout:
            logger.warning("ticker_recap: Yahoo info lookup timed out for %s", ticker)
            return None

    return call


def _default_lookup() -> Callable[[str], TickerInfoDto | None]:
    return _with_timeout(YFinanceClient().get_ticker_info, LOOKUP_TIMEOUT_S)


def _universe(
    env: dict[str, str],
    held_fn: Callable[[], list[HeldTicker]] | None,
    lookup: Callable[[str], TickerInfoDto | None] | None,
) -> dict[str, dict[str, str]]:
    """Popular + held + RECAP_TICKERS. A failed holdings read still recaps the fixed set."""
    try:
        held = (held_fn or _default_held)()
    except Exception:
        logger.exception("ticker_recap: could not read held tickers; recapping the fixed set only")
        held = []
    catalog, stats = build_universe(
        POPULAR_TICKERS,
        held,
        _parse_ticker_env(env.get(RECAP_TICKERS_ENV)),
        # Built only when needed: no held tickers, no Yahoo client.
        lookup=lookup or (_default_lookup() if held else lambda ticker: None),
        cap=_max_tickers(env),
    )
    log_event(logger, EVENT_UNIVERSE, stats)
    return catalog


def _now_for_market(market: str) -> datetime:
    return datetime.now(MARKET_TZ.get(market.upper(), NY_TZ))


def _parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


def _parse_tickers(tickers: str | None, ticker: str | None, default: list[str]) -> list[str]:
    if tickers:
        parsed = [item.strip().upper() for item in tickers.split(",") if item.strip()]
        if parsed:
            return parsed
    if ticker:
        return [ticker.strip().upper()]
    return default


def _extract_status(result) -> str:
    if isinstance(result, dict):
        return str(result.get("status", ""))
    return str(getattr(result, "status", ""))


def _is_success_status(status: str) -> bool:
    return status in {"inserted", "skipped_existing", "replaced"}


def main(
    argv: list[str] | None = None,
    *,
    runner=run_ticker_recap,
    env=None,
    held_fn: Callable[[], list[HeldTicker]] | None = None,
    lookup: Callable[[str], TickerInfoDto | None] | None = None,
) -> int:
    env = os.environ if env is None else env

    parser = argparse.ArgumentParser(description="Run daily per-ticker news recap")
    parser.add_argument("--cadence", default="daily")
    parser.add_argument("--ticker", default=None)
    parser.add_argument("--tickers", default=None, help="Comma-separated tickers, e.g. NVDA,AAPL")
    parser.add_argument("--period-start")
    parser.add_argument("--period-end")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)

    # Explicit tickers skip the holdings read; their names/markets still come from the fixed catalog.
    if args.tickers or args.ticker:
        catalog = _ticker_catalog(env)
        tickers = _parse_tickers(args.tickers, args.ticker, [])
    else:
        catalog = _universe(env, held_fn, lookup)
        tickers = list(catalog)

    explicit_period = bool(args.period_start or args.period_end)
    if args.replace and not explicit_period:
        parser.error("--replace requires --period-start and --period-end")

    period_override: tuple[date, date] | None = None
    if explicit_period:
        if not (args.period_start and args.period_end):
            parser.error("both --period-start and --period-end are required")
        period_override = (_parse_iso_date(args.period_start), _parse_iso_date(args.period_end))

    success_count = 0
    for ticker in tickers:
        meta = catalog.get(ticker, {"name": ticker, "market": market_for_ticker(ticker) or "US"})
        market = meta["market"]
        if period_override is not None:
            period_start, period_end = period_override
        else:
            trading_day = compute_latest_completed_trading_day(market, now=_now_for_market(market))
            period_start = period_end = trading_day

        # One ticker's crash (a held symbol Yahoo/Brave chokes on) must not abort the rest or the audio stage.
        try:
            result = runner(
                ticker=ticker,
                company_name=meta["name"],
                cadence=args.cadence,
                period_start=period_start,
                period_end=period_end,
                market=market,
                replace=args.replace,
            )
        except Exception:
            logger.exception("ticker_recap_crashed ticker=%s", ticker)
            continue
        status = _extract_status(result)
        if _is_success_status(status):
            success_count += 1
        else:
            logger.warning("ticker_recap_failed ticker=%s status=%s", ticker, status)

    if success_count == 0:
        logger.error("ticker_recap_all_failed tickers=%s", ",".join(tickers))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
