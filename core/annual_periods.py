"""
Annual statement period labeling.

`company_financial_statement` is keyed by (company_symbol, period_end_year), so Yahoo's TTM column is
stored in the slot after the latest fiscal year with `is_ttm=True`. When that fiscal year closes, the
next crawl writes the real fiscal-year figures into the same slot, replacing the TTM ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from core.financial_statement_type import FinancialStatementType


@dataclass(frozen=True)
class AnnualPeriod:
    period_end_year: int
    is_ttm: bool
    metrics: dict[str, Any]


@dataclass(frozen=True)
class AnnualRowUpdate:
    is_ttm: bool
    # Statement column -> new value; None clears the column.
    fields: dict[str, Optional[dict[str, Any]]]


def _fiscal_year(value) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def label_annual_periods(items: list[dict]) -> list[AnnualPeriod]:
    """Map scraped periods ({"period_end_year": int | "TTM", "metrics": {...}}) to annual rows."""
    fiscal: list[AnnualPeriod] = []
    ttm_metrics = None
    seen: set[int] = set()
    for item in items:
        metrics = item.get("metrics")
        if not isinstance(metrics, dict) or not metrics:
            continue
        raw_year = item.get("period_end_year")
        if isinstance(raw_year, str) and raw_year.strip().upper() == "TTM":
            ttm_metrics = ttm_metrics or metrics
            continue
        year = _fiscal_year(raw_year)
        if year is None or year in seen:
            continue
        seen.add(year)
        fiscal.append(AnnualPeriod(year, False, metrics))

    if ttm_metrics is None or not fiscal:
        return fiscal
    ttm = AnnualPeriod(max(p.period_end_year for p in fiscal) + 1, True, ttm_metrics)
    return [ttm, *fiscal]


def plan_annual_row_update(
    existing_is_ttm: Optional[bool], period: AnnualPeriod, statement: FinancialStatementType
) -> Optional[AnnualRowUpdate]:
    """How a scraped period changes its row; `existing_is_ttm` is None when no row exists. None = leave as is."""
    if period.is_ttm and existing_is_ttm is False:
        # A fiscal-year row already sits in the TTM slot; never overwrite real figures with TTM ones.
        return None
    fields: dict[str, Optional[dict[str, Any]]] = {statement.value: period.metrics}
    if existing_is_ttm and not period.is_ttm:
        # The TTM slot became a closed fiscal year: drop the other statements' TTM figures so the row
        # never mixes TTM and fiscal-year numbers. Their own crawl refills them.
        fields.update({other.value: None for other in FinancialStatementType if other != statement})
    return AnnualRowUpdate(is_ttm=period.is_ttm, fields=fields)
