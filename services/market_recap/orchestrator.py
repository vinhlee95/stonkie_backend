import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Callable, Literal

from connectors.database import SessionLocal
from services.market_recap.logging import log_event, new_run_id
from services.market_recap.persistence import PersistenceResult, persist_recap
from services.market_recap.recap_generator import GeneratorError, GeneratorResult, generate_recap
from services.market_recap.retrieval import retrieve_candidates
from services.market_recap.validator import ValidationResult, validate_recap

logger = logging.getLogger(__name__)

EVENT_RUN_START = "recap.run.start"
EVENT_RUN_OUTCOME = "recap.run.outcome"


def _safe_raw_candidate(candidate) -> dict:
    return {
        "title": candidate.title,
        "url": candidate.url,
        "published_date": candidate.published_date.isoformat() if candidate.published_date else None,
        "score": candidate.score,
        "provider": candidate.provider,
    }


@dataclass(frozen=True)
class RunResult:
    status: Literal["inserted", "skipped_existing", "replaced", "validation_failed", "generation_failed"]
    inserted: bool
    attempts: int
    validation_failures: list[str]
    validation_warnings: list[str]
    recap_id: int | None


@contextmanager
def _default_session_factory():
    with SessionLocal() as db:
        yield db


def run_market_recap(
    *,
    market: str,
    cadence: str,
    period_start: date,
    period_end: date,
    max_attempts: int = 3,
    replace: bool = False,
    session_factory: Callable = _default_session_factory,
    retrieve_fn: Callable = retrieve_candidates,
    generate_fn: Callable = generate_recap,
    validate_fn: Callable = validate_recap,
    persist_fn: Callable = persist_recap,
) -> RunResult:
    run_id = new_run_id()
    provider = "brave"
    base_fields = {
        "run_id": run_id,
        "market": market,
        "cadence": cadence,
        "provider": provider,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
    }
    log_event(logger, EVENT_RUN_START, dict(base_fields))

    def _outcome(
        *,
        status: str,
        inserted: bool,
        cited_count: int,
        validation_fail_reason: str | None,
    ) -> None:
        stats = retrieval.stats
        log_event(
            logger,
            EVENT_RUN_OUTCOME,
            {
                **base_fields,
                "status": status,
                "queries_total": stats.queries_total,
                "results_total": stats.results_total,
                "fetched_ok": stats.with_raw_content,
                "date_in_window_count": stats.results_total,
                "allowlisted_count": stats.allowlisted,
                "cited_count": cited_count,
                "validation_fail_reason": validation_fail_reason,
                "inserted": inserted,
            },
        )

    retrieval = retrieve_fn(
        market=market,
        period_start=period_start,
        period_end=period_end,
        cadence=cadence,
    )

    last_failures: list[str] = []
    last_warnings: list[str] = []
    last_cited_count = 0
    for attempt in range(1, max_attempts + 1):
        try:
            generated: GeneratorResult = generate_fn(
                market=market,
                cadence=cadence,
                retrieval=retrieval,
                period_start=period_start,
                period_end=period_end,
            )
        except GeneratorError:
            if attempt == max_attempts:
                _outcome(
                    status="generation_failed",
                    inserted=False,
                    cited_count=0,
                    validation_fail_reason=None,
                )
                return RunResult(
                    status="generation_failed",
                    inserted=False,
                    attempts=attempt,
                    validation_failures=[],
                    validation_warnings=[],
                    recap_id=None,
                )
            continue

        last_cited_count = len(generated.payload.sources)
        validation: ValidationResult = validate_fn(
            payload=generated.payload,
            period_start=period_start,
            period_end=period_end,
            market=market,
        )
        last_failures = list(validation.failures)
        last_warnings = list(validation.warnings)
        if not validation.ok:
            if attempt == max_attempts:
                _outcome(
                    status="validation_failed",
                    inserted=False,
                    cited_count=last_cited_count,
                    validation_fail_reason=";".join(last_failures) if last_failures else None,
                )
                return RunResult(
                    status="validation_failed",
                    inserted=False,
                    attempts=attempt,
                    validation_failures=last_failures,
                    validation_warnings=last_warnings,
                    recap_id=None,
                )
            continue

        raw_sources = {
            "candidates": [_safe_raw_candidate(candidate) for candidate in retrieval.candidates],
            "stats": retrieval.stats.model_dump(mode="json"),
            "query_snapshots": retrieval.query_snapshots,
        }
        with session_factory() as db:
            persisted: PersistenceResult = persist_fn(
                db,
                market=market,
                cadence=cadence,
                payload=generated.payload,
                model=generated.model,
                raw_sources=raw_sources,
                replace=replace,
            )
        if persisted.inserted:
            status = "replaced" if persisted.replaced else "inserted"
        else:
            status = "skipped_existing"
        _outcome(
            status=status,
            inserted=persisted.inserted,
            cited_count=last_cited_count,
            validation_fail_reason=None,
        )
        return RunResult(
            status=status,
            inserted=persisted.inserted,
            attempts=attempt,
            validation_failures=[],
            validation_warnings=last_warnings,
            recap_id=persisted.recap_id,
        )

    _outcome(
        status="generation_failed",
        inserted=False,
        cited_count=last_cited_count,
        validation_fail_reason=None,
    )
    return RunResult(
        status="generation_failed",
        inserted=False,
        attempts=max_attempts,
        validation_failures=last_failures,
        validation_warnings=last_warnings,
        recap_id=None,
    )
