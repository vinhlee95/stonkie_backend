from dataclasses import dataclass
from datetime import date, timedelta

from services.market_recap.schemas import RecapPayload
from services.market_recap.source_policy import is_allowlisted

REASON_OUT_OF_WINDOW = "cited_source_out_of_window"
REASON_BULLET_MISSING_ALLOWLISTED = "bullet_missing_allowlisted_source"
WARNING_UNIQUE_SOURCE_FLOOR = "unique_source_floor_below_minimum"


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    failures: list[str]
    warnings: list[str]


def _in_window(value: date, start: date, end: date, grace_days: int) -> bool:
    floor = start - timedelta(days=grace_days)
    ceiling = end + timedelta(days=grace_days)
    return floor <= value <= ceiling


def validate_recap(
    payload: RecapPayload,
    *,
    period_start: date,
    period_end: date,
    grace_days: int = 1,
    min_unique_sources: int = 3,
) -> ValidationResult:
    failures: list[str] = []
    warnings: list[str] = []
    sources_by_id = {source.id: source for source in payload.sources}

    date_failed = False
    allowlist_failed = False

    for bullet in payload.bullets:
        bullet_has_allowlisted = False
        for citation in bullet.citations:
            source = sources_by_id[citation.source_id]
            if not _in_window(source.published_at.date(), period_start, period_end, grace_days):
                date_failed = True
            if is_allowlisted(source.url):
                bullet_has_allowlisted = True
        if not bullet_has_allowlisted:
            allowlist_failed = True

    if date_failed:
        failures.append(REASON_OUT_OF_WINDOW)
    if allowlist_failed:
        failures.append(REASON_BULLET_MISSING_ALLOWLISTED)

    unique_cited_sources = {citation.source_id for bullet in payload.bullets for citation in bullet.citations}
    if len(unique_cited_sources) < min_unique_sources:
        warnings.append(WARNING_UNIQUE_SOURCE_FLOOR)

    return ValidationResult(ok=len(failures) == 0, failures=failures, warnings=warnings)
