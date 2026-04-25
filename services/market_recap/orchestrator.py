import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Callable, Literal

from connectors.database import SessionLocal
from services.market_recap.persistence import PersistenceResult, persist_recap
from services.market_recap.recap_generator import GeneratorError, GeneratorResult, generate_recap
from services.market_recap.retrieval import retrieve_candidates
from services.market_recap.tavily_client import TavilyClient
from services.market_recap.validator import ValidationResult, validate_recap


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
    if retrieve_fn is retrieve_candidates:
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            raise RuntimeError("TAVILY_API_KEY is required for default retrieval")
        retrieval = retrieve_fn(
            market=market,
            period_start=period_start,
            period_end=period_end,
            search_provider=TavilyClient(api_key=api_key),
        )
    else:
        retrieval = retrieve_fn(market=market, period_start=period_start, period_end=period_end)

    last_failures: list[str] = []
    last_warnings: list[str] = []
    for attempt in range(1, max_attempts + 1):
        try:
            generated: GeneratorResult = generate_fn(
                market=market,
                retrieval=retrieval,
                period_start=period_start,
                period_end=period_end,
            )
        except GeneratorError:
            if attempt == max_attempts:
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
            market=market,
        )
        last_failures = list(validation.failures)
        last_warnings = list(validation.warnings)
        if not validation.ok:
            if attempt == max_attempts:
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
            "candidates": [candidate.model_dump(mode="json") for candidate in retrieval.candidates],
            "stats": retrieval.stats.model_dump(mode="json"),
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
        return RunResult(
            status=status,
            inserted=persisted.inserted,
            attempts=attempt,
            validation_failures=[],
            validation_warnings=last_warnings,
            recap_id=persisted.recap_id,
        )

    return RunResult(
        status="generation_failed",
        inserted=False,
        attempts=max_attempts,
        validation_failures=last_failures,
        validation_warnings=last_warnings,
        recap_id=None,
    )
