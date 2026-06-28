"""Run the daily per-ticker news recap for a fixed set of popular US tickers.

Production: runs as Cloud Run job `daily-ticker-recap` (europe-north1), triggered
Tue-Sat 05:00 Helsinki by Cloud Scheduler. Exit code 0 = at least one ticker
succeeded; exit code 1 = every ticker failed/skipped (triggers alert).
See docs/gcp_cronjobs.md for deploy/update/rollback commands.

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
from datetime import date, datetime

from scripts.run_market_recap import (
    MARKET_TZ,
    NY_TZ,
    compute_latest_completed_trading_day,
)
from services.ticker_recap.orchestrator import run_ticker_recap

logger = logging.getLogger(__name__)

# Hardcoded popular US tickers; {ticker: {name, market}} map drives the company
# name in prompts and (future) per-market routing. US-only for now.
POPULAR_TICKERS: dict[str, dict[str, str]] = {
    "NVDA": {"name": "NVIDIA Corporation", "market": "US"},
    "AAPL": {"name": "Apple Inc.", "market": "US"},
    "TSLA": {"name": "Tesla, Inc.", "market": "US"},
    "GOOG": {"name": "Alphabet Inc.", "market": "US"},
}


def _now_for_market(market: str) -> datetime:
    return datetime.now(MARKET_TZ.get(market.upper(), NY_TZ))


def _parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


def _parse_tickers(tickers: str | None, ticker: str | None) -> list[str]:
    if tickers:
        parsed = [item.strip().upper() for item in tickers.split(",") if item.strip()]
        if parsed:
            return parsed
    if ticker:
        return [ticker.strip().upper()]
    return list(POPULAR_TICKERS)


def _extract_status(result) -> str:
    if isinstance(result, dict):
        return str(result.get("status", ""))
    return str(getattr(result, "status", ""))


def _is_success_status(status: str) -> bool:
    return status in {"inserted", "skipped_existing", "replaced"}


def main(argv: list[str] | None = None, *, runner=run_ticker_recap) -> int:
    parser = argparse.ArgumentParser(description="Run daily per-ticker news recap")
    parser.add_argument("--cadence", default="daily")
    parser.add_argument("--ticker", default=None)
    parser.add_argument("--tickers", default=None, help="Comma-separated tickers, e.g. NVDA,AAPL")
    parser.add_argument("--period-start")
    parser.add_argument("--period-end")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)

    tickers = _parse_tickers(args.tickers, args.ticker)

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
        meta = POPULAR_TICKERS.get(ticker, {"name": ticker, "market": "US"})
        market = meta["market"]
        if period_override is not None:
            period_start, period_end = period_override
        else:
            trading_day = compute_latest_completed_trading_day(market, now=_now_for_market(market))
            period_start = period_end = trading_day

        result = runner(
            ticker=ticker,
            company_name=meta["name"],
            cadence=args.cadence,
            period_start=period_start,
            period_end=period_end,
            market=market,
            replace=args.replace,
        )
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
