from copy import deepcopy
from datetime import datetime
from typing import Any, List

from sqlalchemy.inspection import inspect

from connectors.database import SessionLocal
from core.financial_statement_type import FinancialStatementType
from core.quarter_period import quarter_sort_key, sort_quarters_newest_first
from models.company_financial import CompanyFinancials
from models.company_financial_statement import CompanyFinancialStatement
from models.company_quarterly_financial_statement import CompanyQuarterlyFinancialStatement


class CompanyFinancialConnector:
    @classmethod
    def to_dict(cls, model_instance) -> dict[str, Any]:
        """Convert SQLAlchemy model to dictionary, handling datetime fields"""
        result = {}
        for c in inspect(model_instance).mapper.column_attrs:
            value = getattr(model_instance, c.key)
            # Convert datetime objects to ISO format strings
            if isinstance(value, datetime):
                value = value.isoformat()
            result[c.key] = value
        return result

    def _to_dict(self, model_instance) -> dict[str, Any]:
        """Convert SQLAlchemy model to dictionary, handling datetime fields"""
        result = {}
        for c in inspect(model_instance).mapper.column_attrs:
            value = getattr(model_instance, c.key)
            # Convert datetime objects to ISO format strings
            if isinstance(value, datetime):
                value = value.isoformat()
            result[c.key] = value
        return result

    def get_company_statement_by_type(
        self,
        financial_statement_dict: dict[str, Any],
        statement_type: FinancialStatementType | str,
    ) -> dict[str, Any]:
        data = deepcopy(financial_statement_dict)
        st = FinancialStatementType(statement_type) if isinstance(statement_type, str) else statement_type
        for other in FinancialStatementType:
            if other != st:
                data.pop(other, None)
        return data

    def get_company_revenue_data(self, ticker: str) -> List[CompanyFinancials]:
        """Get company revenue data using a fresh session for each request"""
        with SessionLocal() as db:
            return (
                db.query(CompanyFinancials)
                .filter(CompanyFinancials.company_symbol == ticker.upper())
                .order_by(CompanyFinancials.year.desc())
            )

    def get_company_financial_statements(self, ticker: str) -> List[CompanyFinancialStatement]:
        with SessionLocal() as db:
            return (
                db.query(CompanyFinancialStatement)
                .filter(CompanyFinancialStatement.company_symbol == ticker.upper())
                .order_by(CompanyFinancialStatement.period_end_year.desc())
                .all()
            )

    def get_company_quarterly_financial_statements(self, ticker: str) -> List[CompanyQuarterlyFinancialStatement]:
        with SessionLocal() as db:
            return (
                db.query(CompanyQuarterlyFinancialStatement)
                .filter(CompanyQuarterlyFinancialStatement.company_symbol == ticker.upper())
                .all()
            )

    def get_company_financial_statements_by_years(
        self, ticker: str, years: List[int]
    ) -> List[CompanyFinancialStatement]:
        """Fetch annual financial statements for specific years."""
        with SessionLocal() as db:
            return (
                db.query(CompanyFinancialStatement)
                .filter(CompanyFinancialStatement.company_symbol == ticker.upper())
                .filter(CompanyFinancialStatement.period_end_year.in_(years))
                .order_by(CompanyFinancialStatement.period_end_year.desc())
                .all()
            )

    def get_company_financial_statements_recent(self, ticker: str, num_periods: int) -> List[CompanyFinancialStatement]:
        """Fetch most recent N annual financial statements."""
        with SessionLocal() as db:
            return (
                db.query(CompanyFinancialStatement)
                .filter(CompanyFinancialStatement.company_symbol == ticker.upper())
                .order_by(CompanyFinancialStatement.period_end_year.desc())
                .limit(num_periods)
                .all()
            )

    def get_company_quarterly_financial_statements_by_quarters(
        self, ticker: str, quarters: List[str]
    ) -> List[CompanyQuarterlyFinancialStatement]:
        """Fetch quarterly financial statements for specific quarters, newest first."""
        with SessionLocal() as db:
            rows = (
                db.query(CompanyQuarterlyFinancialStatement)
                .filter(CompanyQuarterlyFinancialStatement.company_symbol == ticker.upper())
                .filter(CompanyQuarterlyFinancialStatement.period_end_quarter.in_(quarters))
                .all()
            )
        return sorted(rows, key=lambda row: quarter_sort_key(row.period_end_quarter), reverse=True)

    def get_company_quarterly_financial_statements_recent(
        self, ticker: str, num_periods: int, require_10q_filing: bool = False
    ) -> List[CompanyQuarterlyFinancialStatement]:
        """Fetch most recent N quarterly financial statements, newest first.

        period_end_quarter is a "M/D/YYYY" string, so SQL ordering is lexical; sort by the parsed
        date here (a ticker has ~10 quarterly rows). require_10q_filing skips quarters without a
        10-Q (fiscal Q4 is reported in the 10-K).
        """
        with SessionLocal() as db:
            query = db.query(CompanyQuarterlyFinancialStatement).filter(
                CompanyQuarterlyFinancialStatement.company_symbol == ticker.upper()
            )
            if require_10q_filing:
                query = query.filter(
                    CompanyQuarterlyFinancialStatement.filing_10q_url.isnot(None),
                    CompanyQuarterlyFinancialStatement.filing_10q_url != "",
                )
            rows = query.all()
        rows.sort(key=lambda row: quarter_sort_key(row.period_end_quarter), reverse=True)
        return rows[:num_periods]

    def get_available_periods(self, ticker: str) -> dict[str, list]:
        """Return available annual years and quarterly periods for a ticker. Fast (<10ms)."""
        with SessionLocal() as db:
            annual_rows = (
                db.query(CompanyFinancialStatement.period_end_year)
                .filter(CompanyFinancialStatement.company_symbol == ticker.upper())
                .distinct()
                .all()
            )
            quarterly_rows = (
                db.query(CompanyQuarterlyFinancialStatement.period_end_quarter)
                .filter(CompanyQuarterlyFinancialStatement.company_symbol == ticker.upper())
                .distinct()
                .all()
            )
            return {
                "annual": sorted([r[0] for r in annual_rows], reverse=True),
                "quarterly": sort_quarters_newest_first(r[0] for r in quarterly_rows),
            }

    def get_available_metrics(self, ticker: str) -> list[str]:
        """Return sorted list of unique metric names from the most recent annual statement."""
        with SessionLocal() as db:
            row = (
                db.query(CompanyFinancialStatement)
                .filter(CompanyFinancialStatement.company_symbol == ticker.upper())
                .order_by(CompanyFinancialStatement.period_end_year.desc())
                .first()
            )
            if not row:
                return []
            metrics: set = set()
            for key in FinancialStatementType:
                section = getattr(row, key.value, None)
                if isinstance(section, dict):
                    metrics.update(section.keys())
            return sorted(metrics)

    def get_annual_income_statements(self, ticker: str) -> list[dict[str, Any]]:
        annual_financial_statements = self.get_company_financial_statements(ticker)
        results = []
        for item in annual_financial_statements:
            data = self.get_company_statement_by_type(self._to_dict(item), FinancialStatementType.INCOME_STATEMENT)
            results.append(data)

        return results

    def get_quarterly_income_statements(self, ticker: str) -> list[dict[str, Any]]:
        quarterly_financial_statements = self.get_company_quarterly_financial_statements(ticker)

        results = []
        for item in quarterly_financial_statements:
            data = self.get_company_statement_by_type(self._to_dict(item), FinancialStatementType.INCOME_STATEMENT)
            results.append(data)

        return results

    def get_annual_cash_flow_statements(self, ticker: str) -> list[dict[str, Any]]:
        annual_financial_statements = self.get_company_financial_statements(ticker)
        results = []
        for item in annual_financial_statements:
            data = self.get_company_statement_by_type(self._to_dict(item), FinancialStatementType.CASH_FLOW)
            results.append(data)

        return results

    def get_quarterly_cash_flow_statements(self, ticker: str) -> list[dict[str, Any]]:
        quarterly_financial_statements = self.get_company_quarterly_financial_statements(ticker)

        results = []
        for item in quarterly_financial_statements:
            data = self.get_company_statement_by_type(self._to_dict(item), FinancialStatementType.CASH_FLOW)
            results.append(data)

        return results

    def get_company_filings(self, ticker: str, period: str) -> List[dict[str, Any]]:
        """Get company filings for the specified period (annual or quarterly)"""
        with SessionLocal() as db:
            if period == "annual":
                # Get annual filings from CompanyFinancialStatement
                results = (
                    db.query(CompanyFinancialStatement.filing_10k_url, CompanyFinancialStatement.period_end_year)
                    .filter(CompanyFinancialStatement.company_symbol == ticker.upper())
                    .filter(CompanyFinancialStatement.filing_10k_url.isnot(None))
                    .order_by(CompanyFinancialStatement.period_end_year.desc())
                    .all()
                )

                return [{"url": result.filing_10k_url, "period_end_year": result.period_end_year} for result in results]
            elif period == "quarterly":
                # Get quarterly filings from CompanyQuarterlyFinancialStatement
                results = (
                    db.query(
                        CompanyQuarterlyFinancialStatement.filing_10q_url,
                        CompanyQuarterlyFinancialStatement.period_end_quarter,
                    )
                    .filter(CompanyQuarterlyFinancialStatement.company_symbol == ticker.upper())
                    .filter(CompanyQuarterlyFinancialStatement.filing_10q_url.isnot(None))
                    .all()
                )
                results.sort(key=lambda result: quarter_sort_key(result.period_end_quarter), reverse=True)

                filings = []
                for result in results:
                    # Extract year from period_end_quarter (format: "MM/DD/YYYY")
                    period_end_year = None
                    try:
                        if result.period_end_quarter:
                            # Parse date string to extract year
                            date_parts = result.period_end_quarter.split("/")
                            if len(date_parts) == 3:
                                period_end_year = int(date_parts[2])
                    except (ValueError, IndexError):
                        # If parsing fails, period_end_year will remain None
                        pass

                    filings.append(
                        {
                            "url": result.filing_10q_url,
                            "period_end_quarter": result.period_end_quarter,
                            "period_end_year": period_end_year,
                        }
                    )

                return filings
            else:
                return []

    def get_company_filing_url(self, ticker: str, period_end_at: str, period_type: str) -> str | None:
        """Get the filing URL for a specific ticker and period"""
        with SessionLocal() as db:
            if period_type == "annually":
                result = (
                    db.query(CompanyFinancialStatement.filing_10k_url)
                    .filter(CompanyFinancialStatement.company_symbol == ticker.upper())
                    .filter(CompanyFinancialStatement.period_end_year == int(period_end_at))
                    .first()
                )

                return result.filing_10k_url if result else None
            elif period_type == "quarterly":
                result = (
                    db.query(CompanyQuarterlyFinancialStatement.filing_10q_url)
                    .filter(CompanyQuarterlyFinancialStatement.company_symbol == ticker.upper())
                    .filter(CompanyQuarterlyFinancialStatement.period_end_quarter == period_end_at)
                    .first()
                )

                return result.filing_10q_url if result else None
            else:
                return None
