"""Optimizes financial data fetching based on question requirements."""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from connectors.company_financial import CompanyFinancialConnector
from external_knowledge.company_fundamental import get_company_fundamental

from .types import FinancialDataRequirement, FinancialPeriodRequirement

logger = logging.getLogger(__name__)


class FinancialDataOptimizer:
    """Fetches only the required financial data based on question analysis."""

    def __init__(self, company_financial_connector: Optional[CompanyFinancialConnector] = None):
        """
        Initialize the optimizer.

        Args:
            company_financial_connector: Connector for financial data. Creates default if not provided.
        """
        self.company_financial_connector = company_financial_connector or CompanyFinancialConnector()

    async def fetch_optimized_data(
        self,
        ticker: str,
        data_requirement: FinancialDataRequirement,
        period_requirement: Optional[FinancialPeriodRequirement] = None,
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Fetch only the required financial data.

        Args:
            ticker: Company ticker symbol
            data_requirement: Level of data required (NONE, BASIC, DETAILED, QUARTERLY_SUMMARY)
            period_requirement: Which specific periods to fetch (only used if DETAILED or QUARTERLY_SUMMARY)

        Returns:
            Tuple of (company_fundamental, annual_statements, quarterly_statements)
        """
        company_fundamental = None
        annual_statements: List[Dict[str, Any]] = []
        quarterly_statements: List[Dict[str, Any]] = []

        # Fetch basic company data if needed
        if data_requirement in [FinancialDataRequirement.BASIC]:
            t_start = time.perf_counter()
            company_fundamental = get_company_fundamental(ticker)
            t_end = time.perf_counter()
            logger.info(f"Profiling get_company_fundamental: {t_end - t_start:.4f}s")

        # Fetch quarterly summary data (minimal: just 1 quarter with filing URL)
        if data_requirement == FinancialDataRequirement.QUARTERLY_SUMMARY:
            t_start = time.perf_counter()
            quarterly_statements = await self._fetch_quarterly_summary(ticker, period_requirement)
            t_end = time.perf_counter()
            logger.info(f"Profiling fetch_quarterly_summary: {t_end - t_start:.4f}s")
            logger.info(f"Fetched {len(quarterly_statements)} quarterly statement(s) for summary")

        # Fetch annual summary data (minimal: just 1 year with filing URL)
        if data_requirement == FinancialDataRequirement.ANNUAL_SUMMARY:
            t_start = time.perf_counter()
            annual_statements = await self._fetch_annual_summary(ticker, period_requirement)
            t_end = time.perf_counter()
            logger.info(f"Profiling fetch_annual_summary: {t_end - t_start:.4f}s")
            logger.info(f"Fetched {len(annual_statements)} annual statement(s) for summary")

        # Fetch detailed financial statements only if required
        if data_requirement == FinancialDataRequirement.DETAILED and period_requirement:
            t_start = time.perf_counter()

            # Fetch annual statements if needed
            if period_requirement.period_type in ["annual", "both"]:
                annual_statements = await self._fetch_annual_statements(ticker, period_requirement)

            # Fetch quarterly statements if needed
            if period_requirement.period_type in ["quarterly", "both"]:
                quarterly_statements = await self._fetch_quarterly_statements(ticker, period_requirement)

            t_end = time.perf_counter()
            logger.info(f"Profiling get_financial_statements (optimized): {t_end - t_start:.4f}s")
            logger.info(f"Fetched {len(annual_statements)} annual + {len(quarterly_statements)} quarterly statements")

        return company_fundamental, annual_statements, quarterly_statements

    async def _fetch_annual_statements(
        self, ticker: str, period_requirement: FinancialPeriodRequirement
    ) -> List[Dict[str, Any]]:
        """
        Fetch annual financial statements based on period requirement.

        Args:
            ticker: Company ticker symbol
            period_requirement: Period specification

        Returns:
            List of annual statement dictionaries
        """
        if period_requirement.specific_years:
            statements_raw = self.company_financial_connector.get_company_financial_statements_by_years(
                ticker, period_requirement.specific_years
            )
            logger.info(
                f"Fetched {len(statements_raw)} annual statements for years: {period_requirement.specific_years}"
            )
        elif period_requirement.num_periods:
            statements_raw = self.company_financial_connector.get_company_financial_statements_recent(
                ticker, period_requirement.num_periods
            )
            logger.info(f"Fetched {len(statements_raw)} most recent annual statements")
        else:
            # Fallback: get last 3 years by default
            statements_raw = self.company_financial_connector.get_company_financial_statements_recent(ticker, 3)
            logger.info(f"Fetched {len(statements_raw)} annual statements (default: 3 most recent)")

        return [CompanyFinancialConnector.to_dict(item) for item in statements_raw]

    async def _fetch_quarterly_statements(
        self, ticker: str, period_requirement: FinancialPeriodRequirement
    ) -> List[Dict[str, Any]]:
        """
        Fetch quarterly financial statements based on period requirement.

        Args:
            ticker: Company ticker symbol
            period_requirement: Period specification

        Returns:
            List of quarterly statement dictionaries
        """
        if period_requirement.specific_quarters:
            statements_raw = self.company_financial_connector.get_company_quarterly_financial_statements_by_quarters(
                ticker, period_requirement.specific_quarters
            )
            logger.info(
                f"Fetched {len(statements_raw)} quarterly statements for: {period_requirement.specific_quarters}"
            )
        elif period_requirement.num_periods:
            statements_raw = self.company_financial_connector.get_company_quarterly_financial_statements_recent(
                ticker, period_requirement.num_periods
            )
            logger.info(f"Fetched {len(statements_raw)} most recent quarterly statements")
        else:
            # Fallback: get last 4 quarters by default
            statements_raw = self.company_financial_connector.get_company_quarterly_financial_statements_recent(
                ticker, 4
            )
            logger.info(f"Fetched {len(statements_raw)} quarterly statements (default: 4 most recent)")

        return [CompanyFinancialConnector.to_dict(item) for item in statements_raw]

    async def _fetch_quarterly_summary(
        self, ticker: str, period_requirement: Optional[FinancialPeriodRequirement] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch quarterly statement for summary questions (only the latest or a specific quarter).
        Returns minimal data with filing URL included.

        Args:
            ticker: Company ticker symbol
            period_requirement: Period specification (optional, defaults to latest)

        Returns:
            List with single quarterly statement dictionary including filing_10q_url
        """
        # Check if a specific quarter is requested (but not "latest" placeholder)
        if (
            period_requirement
            and period_requirement.specific_quarters
            and period_requirement.specific_quarters != ["latest"]
        ):
            statements_raw = self.company_financial_connector.get_company_quarterly_financial_statements_by_quarters(
                ticker, period_requirement.specific_quarters
            )
            logger.info(
                f"Fetched {len(statements_raw)} quarterly statement(s) for: {period_requirement.specific_quarters}"
            )
        elif period_requirement and period_requirement.num_periods:
            # Use num_periods if specified (typically 1 for summary questions)
            statements_raw = self.company_financial_connector.get_company_quarterly_financial_statements_recent(
                ticker, period_requirement.num_periods
            )
            logger.info(f"Fetched {period_requirement.num_periods} most recent quarterly statement(s) for summary")
        else:
            # Default: fetch only the most recent quarter
            statements_raw = self.company_financial_connector.get_company_quarterly_financial_statements_recent(
                ticker, 1
            )
            logger.info("Fetched latest quarterly statement for summary")

        # Convert to dict - filing_10q_url is already included in the model
        statements_dict = [CompanyFinancialConnector.to_dict(item) for item in statements_raw]

        # Filter out statements that don't have a filing_10q_url
        filtered_statements = [
            stmt
            for stmt in statements_dict
            if stmt.get("filing_10q_url") is not None and stmt.get("filing_10q_url").strip()
        ]

        logger.info(
            f"Filtered to {len(filtered_statements)} quarterly statement(s) with valid filing URLs out of {len(statements_dict)} total"
        )
        return filtered_statements

    async def _fetch_annual_summary(
        self, ticker: str, period_requirement: Optional[FinancialPeriodRequirement] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch annual statement for summary questions (only the latest or a specific year).
        Returns minimal data with filing URL included.

        Args:
            ticker: Company ticker symbol
            period_requirement: Period specification (optional, defaults to latest)

        Returns:
            List with single annual statement dictionary including filing_10k_url
        """
        # Check if specific years are requested
        if period_requirement and period_requirement.specific_years:
            statements_raw = self.company_financial_connector.get_company_financial_statements_by_years(
                ticker, period_requirement.specific_years
            )
            logger.info(
                f"Fetched {len(statements_raw)} annual statement(s) for years: {period_requirement.specific_years}"
            )
        elif period_requirement and period_requirement.num_periods:
            # Use num_periods if specified (typically 1 for summary questions)
            statements_raw = self.company_financial_connector.get_company_financial_statements_recent(
                ticker, period_requirement.num_periods
            )
            logger.info(f"Fetched {period_requirement.num_periods} most recent annual statement(s) for summary")
        else:
            # Default: fetch only the most recent year
            statements_raw = self.company_financial_connector.get_company_financial_statements_recent(ticker, 1)
            logger.info("Fetched latest annual statement for summary")

        # Convert to dict - filing_10k_url is already included in the model
        statements_dict = [CompanyFinancialConnector.to_dict(item) for item in statements_raw]

        # Filter out statements that don't have a filing_10k_url
        filtered_statements = [
            stmt
            for stmt in statements_dict
            if stmt.get("filing_10k_url") is not None and stmt.get("filing_10k_url").strip()
        ]

        logger.info(
            f"Filtered to {len(filtered_statements)} annual statement(s) with valid filing URLs out of {len(statements_dict)} total"
        )
        return filtered_statements
