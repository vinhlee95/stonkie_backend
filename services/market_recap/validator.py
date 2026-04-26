from dataclasses import dataclass
from datetime import date, timedelta

from services.market_recap.schemas import RecapPayload
from services.market_recap.source_policy import is_allowlisted

REASON_OUT_OF_WINDOW = "cited_source_out_of_window"
REASON_BULLET_MISSING_ALLOWLISTED = "bullet_missing_allowlisted_source"
REASON_EMPTY_SUMMARY = "empty_summary"
REASON_EMPTY_BULLETS = "empty_bullets"
REASON_EMPTY_SOURCES = "empty_sources"
REASON_VN_INDEX_MISSING = "vn_index_missing"
REASON_VN_MACRO_CONTEXT_MISSING = "vn_macro_context_missing"
REASON_VN_MONEY_FLOW_MISSING = "vn_money_flow_missing"
REASON_VN_RECAP_ALLOWLIST_FLOOR = "vn_recap_allowlist_floor_below_minimum"
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
    market: str = "US",
    grace_days: int = 1,
    min_unique_sources: int = 3,
) -> ValidationResult:
    failures: list[str] = []
    warnings: list[str] = []

    if not payload.summary.strip():
        failures.append(REASON_EMPTY_SUMMARY)
    if not payload.bullets:
        failures.append(REASON_EMPTY_BULLETS)
    if not payload.sources:
        failures.append(REASON_EMPTY_SOURCES)

    if failures:
        return ValidationResult(ok=False, failures=failures, warnings=warnings)

    if market.upper() == "VN":
        # VN content-marker checks are intentionally skipped for now.
        # Phase 6.7 introduces LLM-based semantic validation.
        pass

    sources_by_id = {source.id: source for source in payload.sources}

    date_failed = False
    allowlist_failed = False
    allowlisted_source_ids: set[str] = set()
    is_vn_market = market.upper() == "VN"

    for bullet in payload.bullets:
        bullet_has_allowlisted = False
        for citation in bullet.citations:
            source = sources_by_id[citation.source_id]
            if not _in_window(source.published_at.date(), period_start, period_end, grace_days):
                date_failed = True
            if is_allowlisted(source.url, market=market):
                bullet_has_allowlisted = True
                allowlisted_source_ids.add(citation.source_id)
        if not bullet_has_allowlisted:
            allowlist_failed = True

    if date_failed:
        failures.append(REASON_OUT_OF_WINDOW)
    if is_vn_market and allowlist_failed:
        warnings.append(REASON_BULLET_MISSING_ALLOWLISTED)
    elif allowlist_failed:
        failures.append(REASON_BULLET_MISSING_ALLOWLISTED)
    if is_vn_market and len(allowlisted_source_ids) < 2:
        failures.append(REASON_VN_RECAP_ALLOWLIST_FLOOR)

    unique_cited_sources = {citation.source_id for bullet in payload.bullets for citation in bullet.citations}
    if len(unique_cited_sources) < min_unique_sources:
        warnings.append(WARNING_UNIQUE_SOURCE_FLOOR)

    return ValidationResult(ok=len(failures) == 0, failures=failures, warnings=warnings)
