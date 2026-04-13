"""Financial statement blob keys (annual/quarterly JSON columns and API report_type)."""

from __future__ import annotations

from enum import StrEnum


class FinancialStatementType(StrEnum):
    INCOME_STATEMENT = "income_statement"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW = "cash_flow"

    @classmethod
    def all_ordered(cls) -> tuple[FinancialStatementType, ...]:
        """Canonical order (e.g. classifier relevant_statements when all types apply)."""
        return (cls.INCOME_STATEMENT, cls.BALANCE_SHEET, cls.CASH_FLOW)

    @classmethod
    def crawl_dispatch_order(cls) -> tuple[FinancialStatementType, ...]:
        """Order used when dispatching crawl tasks (matches historical behavior)."""
        return (cls.BALANCE_SHEET, cls.CASH_FLOW, cls.INCOME_STATEMENT)

    def yahoo_finance_path_segment(self) -> str:
        """Path segment under /quote/{ticker}/ for Yahoo Finance statement pages."""
        return {
            FinancialStatementType.INCOME_STATEMENT: "financials",
            FinancialStatementType.BALANCE_SHEET: "balance-sheet",
            FinancialStatementType.CASH_FLOW: "cash-flow",
        }[self]
