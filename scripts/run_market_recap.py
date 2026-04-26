"""Run weekly market recap orchestration.

Production: runs as Cloud Run job `weekly-market-recap` (europe-north1), triggered every
Saturday at 08:00 Helsinki by Cloud Scheduler `weekly-market-recap-scheduler` (europe-west1).
Exit code 0 = all markets succeeded; exit code 1 = at least one market failed (triggers alert).
See docs/gcp_cronjobs.md for deploy/update/rollback commands.

Local usage:
    source venv/bin/activate
    PYTHONPATH=. python scripts/run_market_recap.py --markets US,VN,FI
    PYTHONPATH=. python scripts/run_market_recap.py --market US --period-start 2026-04-20 --period-end 2026-04-24
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from services.market_recap.orchestrator import run_market_recap

NY_TZ = ZoneInfo("America/New_York")
MAX_BACKFILL_WEEKS = 8

MARKET_TZ = {
    "US": ZoneInfo("America/New_York"),
    "VN": ZoneInfo("Asia/Ho_Chi_Minh"),
    "FI": ZoneInfo("Europe/Helsinki"),
}


def _now_for_market(market: str) -> datetime:
    return datetime.now(MARKET_TZ.get(market.upper(), NY_TZ))


def compute_latest_completed_week(*, now: datetime | None = None) -> tuple[date, date]:
    current = now or datetime.now(NY_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=NY_TZ)
    current_day = current.date()
    last_friday = current_day - timedelta(days=(current_day.weekday() - 4) % 7 or 7)
    last_monday = last_friday - timedelta(days=4)
    return last_monday, last_friday


def compute_latest_completed_trading_day(market: str, *, now: datetime | None = None) -> date:
    tz = MARKET_TZ.get(market.upper(), NY_TZ)
    current = now or datetime.now(tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    current_day = current.date()
    candidate = current_day - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


def _weekly_periods(start: date, end: date) -> list[tuple[date, date]]:
    if start > end:
        raise ValueError("backfill-start must be <= backfill-end")
    periods: list[tuple[date, date]] = []
    current_start = start
    while current_start <= end:
        current_end = min(current_start + timedelta(days=4), end)
        periods.append((current_start, current_end))
        current_start = current_start + timedelta(days=7)
    return periods


def _extract_status(result) -> str:
    if isinstance(result, dict):
        return str(result.get("status", ""))
    return str(getattr(result, "status", ""))


def _is_success_status(status: str) -> bool:
    return status in {"inserted", "skipped_existing", "replaced"}


def _parse_markets(markets: str | None, market: str) -> list[str]:
    if markets:
        parsed = [item.strip().upper() for item in markets.split(",") if item.strip()]
        if parsed:
            return parsed
    return [market.upper()]


def main(argv: list[str] | None = None, *, runner=run_market_recap) -> int:
    parser = argparse.ArgumentParser(description="Run weekly US market recap")
    parser.add_argument("--market", default="US")
    parser.add_argument("--markets", default=None, help="Comma-separated markets, e.g. US,VN")
    parser.add_argument("--cadence", default="weekly")
    parser.add_argument("--period-start")
    parser.add_argument("--period-end")
    parser.add_argument("--backfill-start")
    parser.add_argument("--backfill-end")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    cadence = args.cadence.lower()

    explicit_period = bool(args.period_start or args.period_end)
    explicit_backfill = bool(args.backfill_start or args.backfill_end)
    markets = _parse_markets(args.markets, args.market)
    if explicit_period and explicit_backfill:
        parser.error("cannot combine explicit period and backfill flags")

    if args.replace and not explicit_period:
        parser.error("--replace requires --period-start and --period-end")

    if explicit_period:
        if not (args.period_start and args.period_end):
            parser.error("both --period-start and --period-end are required")
        period_start = _parse_iso_date(args.period_start)
        period_end = _parse_iso_date(args.period_end)
        for market in markets:
            result = runner(
                market=market,
                cadence=args.cadence,
                period_start=period_start,
                period_end=period_end,
                replace=args.replace,
            )
            if not _is_success_status(_extract_status(result)):
                return 1
        return 0

    if cadence == "daily":
        if explicit_backfill:
            parser.error("--backfill flags are not supported for --cadence daily")
        for market in markets:
            trading_day = compute_latest_completed_trading_day(market, now=_now_for_market(market))
            result = runner(
                market=market,
                cadence=args.cadence,
                period_start=trading_day,
                period_end=trading_day,
                replace=False,
            )
            if not _is_success_status(_extract_status(result)):
                return 1
        return 0

    if explicit_backfill:
        if not (args.backfill_start and args.backfill_end):
            parser.error("both --backfill-start and --backfill-end are required")
        backfill_start = _parse_iso_date(args.backfill_start)
        backfill_end = _parse_iso_date(args.backfill_end)
        periods = _weekly_periods(backfill_start, backfill_end)
        if len(periods) > MAX_BACKFILL_WEEKS:
            parser.error(f"backfill span exceeds max of {MAX_BACKFILL_WEEKS} weeks")
        for period_start, period_end in periods:
            for market in markets:
                result = runner(
                    market=market,
                    cadence=args.cadence,
                    period_start=period_start,
                    period_end=period_end,
                    replace=False,
                )
                if not _is_success_status(_extract_status(result)):
                    return 1
        return 0

    period_start, period_end = compute_latest_completed_week()
    for market in markets:
        result = runner(
            market=market,
            cadence=args.cadence,
            period_start=period_start,
            period_end=period_end,
            replace=False,
        )
        if not _is_success_status(_extract_status(result)):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
