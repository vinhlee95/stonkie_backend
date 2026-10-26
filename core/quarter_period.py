"""
Quarterly period helpers.

`period_end_quarter` is stored as a "M/D/YYYY" string, so SQL/str ordering is lexical
("9/30/2025" > "6/30/2026"). Parse to a date before sorting or picking the latest quarter.
"""

from datetime import date, datetime
from typing import Iterable, Optional


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
