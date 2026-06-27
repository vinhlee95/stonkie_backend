from dataclasses import dataclass
from datetime import date, timedelta

from services.ticker_recap.schemas import TickerRecapPayload

REASON_EMPTY_SUMMARY = "empty_summary"
REASON_BULLET_COUNT = "bullet_count_out_of_range"
REASON_BULLET_MISSING_CITATION = "bullet_missing_citation"
REASON_OUT_OF_WINDOW = "cited_source_out_of_window"
REASON_CITATION_UNKNOWN_SOURCE = "citation_references_unknown_source"

MIN_BULLETS = 3
MAX_BULLETS = 6


def _in_window(value: date, start: date, end: date, grace_days: int) -> bool:
    return start - timedelta(days=grace_days) <= value <= end + timedelta(days=grace_days)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    failures: list[str]
    warnings: list[str]


def validate_recap(
    payload: TickerRecapPayload,
    *,
    period_start: date,
    period_end: date,
    ticker: str,
    grace_days: int = 1,
) -> ValidationResult:
    failures: list[str] = []
    warnings: list[str] = []

    if not payload.summary.strip():
        failures.append(REASON_EMPTY_SUMMARY)
    if not MIN_BULLETS <= len(payload.bullets) <= MAX_BULLETS:
        failures.append(REASON_BULLET_COUNT)

    if any(not bullet.citations for bullet in payload.bullets):
        failures.append(REASON_BULLET_MISSING_CITATION)

    sources_by_id = {source.id: source for source in payload.sources}

    out_of_window = False
    unknown_source = False
    for bullet in payload.bullets:
        for citation in bullet.citations:
            source = sources_by_id.get(citation.source_id)
            if source is None:
                unknown_source = True
                continue
            if not _in_window(source.published_at.date(), period_start, period_end, grace_days):
                out_of_window = True
    if out_of_window:
        failures.append(REASON_OUT_OF_WINDOW)
    if unknown_source:
        failures.append(REASON_CITATION_UNKNOWN_SOURCE)

    return ValidationResult(ok=len(failures) == 0, failures=failures, warnings=warnings)
