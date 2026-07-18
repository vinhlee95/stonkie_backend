"""Generate spoken audio for recaps that don't have it yet.

Runs after the recap jobs (see Dockerfile.market-recap.daily / Dockerfile.ticker-recap).
Idempotent: only picks up rows where `audio_key IS NULL`, so a re-run costs nothing
and a missed run self-heals on the next pass.

Exit code 0 = all attempted recaps succeeded (or none were pending);
exit code 1 = at least one failed.

Local usage:
    source venv/bin/activate
    PYTHONPATH=. python scripts/run_recap_audio.py --cadence daily
    PYTHONPATH=. python scripts/run_recap_audio.py --cadence daily --kind market --limit 3
    PYTHONPATH=. python scripts/run_recap_audio.py --cadence daily --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, timedelta

from connectors.market_recap import MarketRecapConnector
from connectors.ticker_recap import TickerRecapConnector
from services.recap_audio import RecapAudioService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def _run(*, cadence: str, kind: str, limit: int, since_days: int, dry_run: bool) -> int:
    market_connector = MarketRecapConnector()
    ticker_connector = TickerRecapConnector()

    # Bound how far back we look. Without this the job walks the whole archive
    # once the fresh rows are done -- an unintended (and billable) backfill.
    # A few days of slack lets a missed run self-heal without going historical.
    since = date.today() - timedelta(days=since_days) if since_days >= 0 else None

    pending: list[tuple[str, object]] = []
    if kind in ("market", "all"):
        pending += [
            ("market", d) for d in market_connector.get_without_audio(cadence=cadence, limit=limit, since=since)
        ]
    if kind in ("ticker", "all"):
        pending += [
            ("ticker", d) for d in ticker_connector.get_without_audio(cadence=cadence, limit=limit, since=since)
        ]

    if not pending:
        logger.info("recap_audio.job.nothing_pending cadence=%s kind=%s", cadence, kind)
        return 0

    logger.info("recap_audio.job.start cadence=%s kind=%s pending=%d", cadence, kind, len(pending))
    if dry_run:
        for kind_name, dto in pending:
            label = getattr(dto, "market", None) or getattr(dto, "ticker", "?")
            logger.info("recap_audio.job.dry_run %s %s id=%s", kind_name, label, dto.id)
        return 0

    service = RecapAudioService(
        market_connector=market_connector,
        ticker_connector=ticker_connector,
    )

    failures = 0
    for kind_name, dto in pending:
        label = getattr(dto, "market", None) or getattr(dto, "ticker", "?")
        try:
            if kind_name == "market":
                result = await service.generate_for_market_recap(dto)
            else:
                result = await service.generate_for_ticker_recap(dto)
            logger.info(
                "recap_audio.job.ok %s %s id=%s key=%s duration=%.1fs warnings=%d",
                kind_name,
                label,
                dto.id,
                result.audio_key,
                result.duration_s,
                len(result.figure_warnings),
            )
        except Exception:
            failures += 1
            logger.exception("recap_audio.job.failed %s %s id=%s", kind_name, label, dto.id)

    logger.info(
        "recap_audio.job.done attempted=%d failed=%d",
        len(pending),
        failures,
    )
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate audio for recaps missing it")
    parser.add_argument("--cadence", default="daily", choices=["daily", "weekly"])
    parser.add_argument("--kind", default="all", choices=["market", "ticker", "all"])
    parser.add_argument("--limit", type=int, default=50, help="max recaps per kind")
    parser.add_argument(
        "--since-days",
        type=int,
        default=3,
        help="only consider recaps whose period starts within this many days (-1 = no bound, backfills history)",
    )
    parser.add_argument("--dry-run", action="store_true", help="list pending recaps without generating")
    args = parser.parse_args()

    sys.exit(
        asyncio.run(
            _run(
                cadence=args.cadence,
                kind=args.kind,
                limit=args.limit,
                since_days=args.since_days,
                dry_run=args.dry_run,
            )
        )
    )


if __name__ == "__main__":
    main()
