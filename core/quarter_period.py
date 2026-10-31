"""
Quarterly period helpers.

`period_end_quarter` is stored as a "M/D/YYYY" string, so SQL/str ordering is lexical
("9/30/2025" > "6/30/2026"). Parse to a date before sorting or picking the latest quarter.
"""

import re
from datetime import date, datetime
from typing import Iterable, Optional, Tuple

_QUARTER_LABEL = re.compile(r"^(\d{4})-Q([1-4])$", re.IGNORECASE)


def parse_quarter_end(value: Optional[str]) -> Optional[date]:
    """Parse a "M/D/YYYY" period end; None when missing or malformed."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def quarter_sort_key(value: Optional[str]) -> date:
    """Sort key by real period-end date; unparseable values sort as oldest."""
    return parse_quarter_end(value) or date.min


def sort_quarters_newest_first(values: Iterable[Optional[str]]) -> list[Optional[str]]:
    return sorted(values, key=quarter_sort_key, reverse=True)


def parse_quarter_label(value: Optional[str]) -> Optional[Tuple[int, int]]:
    """Parse a router label like "2026-Q3" to (2026, 3); None for anything else (e.g. "latest")."""
    match = _QUARTER_LABEL.match((value or "").strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


def calendar_quarter(value: Optional[str]) -> Optional[Tuple[int, int]]:
    """Calendar (year, quarter) a "M/D/YYYY" period end falls in; None when unparseable."""
    period_end = parse_quarter_end(value)
    return (period_end.year, (period_end.month - 1) // 3 + 1) if period_end else None
