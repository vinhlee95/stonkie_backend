import logging
from dataclasses import dataclass
from datetime import date
from typing import Callable, Literal

from connectors.ticker_recap import TickerRecapConnector, UpsertResult
from connectors.yfinance_client import YFinanceClient
from services.market_recap.logging import log_event, new_run_id
from services.price_change import get_price_changes
from services.ticker_recap.query_generator import generate_query
from services.ticker_recap.recap_generator import GeneratorError, GeneratorResult, generate_recap
from services.ticker_recap.retrieval import retrieve_for_ticker
from services.ticker_recap.validator import ValidationResult, validate_recap

logger = logging.getLogger(__name__)

EVENT_RUN_START = "ticker_recap.run.start"
EVENT_RUN_OUTCOME = "ticker_recap.run.outcome"

RunStatus = Literal[
    "inserted",
    "skipped_existing",
    "replaced",
    "validation_failed",
    "generation_failed",
    "skipped_no_price",
    "skipped_no_results",
]


@dataclass(frozen=True)
class RunResult:
    status: RunStatus
    inserted: bool
    attempts: int
    validation_failures: list[str]
    validation_warnings: list[str]
    recap_id: int | None


def _default_price_fn(ticker: str) -> dict | None:
    return get_price_changes([ticker], YFinanceClient()).get(ticker)


def _safe_raw_candidate(candidate) -> dict:
    return {
        "title": candidate.title,
        "url": candidate.url,
        "published_date": candidate.published_date.isoformat() if candidate.published_date else None,
        "score": candidate.score,
        "provider": candidate.provider,
    }


def run_ticker_recap(
    *,
    ticker: str,
    company_name: str,
    cadence: str,
    period_start: date,
    period_end: date,
    market: str = "US",
    max_attempts: int = 3,
    replace: bool = False,
    price_fn: Callable = _default_price_fn,
    query_fn: Callable = generate_query,
    retrieve_fn: Callable = retrieve_for_ticker,
    generate_fn: Callable = generate_recap,
    validate_fn: Callable = validate_recap,
    recap_connector: TickerRecapConnector | None = None,
) -> RunResult:
    connector = recap_connector or TickerRecapConnector()
    run_id = new_run_id()
    base_fields = {
        "run_id": run_id,
        "ticker": ticker,
        "cadence": cadence,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
    }
    log_event(logger, EVENT_RUN_START, dict(base_fields))

    def _outcome(*, status: str) -> None:
        log_event(logger, EVENT_RUN_OUTCOME, {**base_fields, "status": status})

    price_change = price_fn(ticker)
    if price_change is None:
        logger.warning("ticker_recap: no price change for %s; skipping", ticker)
        _outcome(status="skipped_no_price")
        return RunResult(
            status="skipped_no_price",
            inserted=False,
            attempts=0,
            validation_failures=[],
            validation_warnings=[],
            recap_id=None,
        )

    query = query_fn(ticker=ticker, company_name=company_name, price_change=price_change)

    retrieval = retrieve_fn(
        ticker=ticker,
        company_name=company_name,
        query=query,
        period_start=period_start,
        period_end=period_end,
        market=market,
    )
    if not retrieval.candidates:
        logger.warning("ticker_recap: zero in-window candidates for %s; skipping", ticker)
        _outcome(status="skipped_no_results")
        return RunResult(
            status="skipped_no_results",
            inserted=False,
            attempts=0,
            validation_failures=[],
            validation_warnings=[],
            recap_id=None,
        )

    last_warnings: list[str] = []
    for attempt in range(1, max_attempts + 1):
        try:
            generated: GeneratorResult = generate_fn(
                retrieval=retrieval,
                ticker=ticker,
                company_name=company_name,
                price_change=price_change,
                period_start=period_start,
                period_end=period_end,
                cadence=cadence,
            )
        except GeneratorError:
            if attempt == max_attempts:
                logger.warning("ticker_recap: generation failed for %s after %d attempts", ticker, attempt)
                _outcome(status="generation_failed")
                return RunResult(
                    status="generation_failed",
                    inserted=False,
                    attempts=attempt,
                    validation_failures=[],
                    validation_warnings=[],
                    recap_id=None,
                )
            continue

        validation: ValidationResult = validate_fn(
            payload=generated.payload,
            period_start=period_start,
            period_end=period_end,
            ticker=ticker,
        )
        last_warnings = list(validation.warnings)
        if not validation.ok:
            if attempt == max_attempts:
                logger.warning("ticker_recap: validation failed for %s: %s", ticker, ";".join(validation.failures))
                _outcome(status="validation_failed")
                return RunResult(
                    status="validation_failed",
                    inserted=False,
                    attempts=attempt,
                    validation_failures=list(validation.failures),
                    validation_warnings=last_warnings,
                    recap_id=None,
                )
            continue

        raw_sources = {
            "candidates": [_safe_raw_candidate(c) for c in retrieval.candidates],
            "stats": retrieval.stats.model_dump(mode="json"),
            "query_snapshots": retrieval.query_snapshots,
        }
        persisted: UpsertResult = connector.upsert_recap(
            ticker=ticker,
            cadence=cadence,
            payload=generated.payload,
            model=generated.model,
            raw_sources=raw_sources,
            price_change=price_change,
            search_query=query,
            replace=replace,
        )
        if persisted.inserted:
            status = "replaced" if persisted.replaced else "inserted"
        else:
            status = "skipped_existing"
        _outcome(status=status)
        return RunResult(
            status=status,
            inserted=persisted.inserted,
            attempts=attempt,
            validation_failures=[],
            validation_warnings=last_warnings,
            recap_id=persisted.recap_id,
        )
