"""Router quarter labels ("2026-Q3", "latest") never match the DB's "M/D/YYYY" keys (Q12).

When they match nothing, the optimizer falls back to the most recent quarters instead of
returning no statements (which made the answer model invent figures).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import services.question_analyzer.data_optimizer as data_optimizer_module
from services.question_analyzer.data_optimizer import FinancialDataOptimizer
from services.question_analyzer.types import FinancialDataRequirement, FinancialPeriodRequirement

ROW = {"period_end_quarter": "3/31/2026", "filing_10q_url": "https://sec.gov/q.htm"}


@pytest.fixture()
def connector(monkeypatch):
    conn = MagicMock()
    conn.get_company_quarterly_financial_statements_by_quarters.return_value = []
    conn.get_company_quarterly_financial_statements_recent.return_value = [SimpleNamespace()]
    monkeypatch.setattr(data_optimizer_module.CompanyFinancialConnector, "to_dict", staticmethod(lambda _row: ROW))
    return conn


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("specific_quarters", "num_periods", "expected_n"),
    [
        (["latest"], 1, 1),
        (["2026-Q3"], None, 1),
        (["2026-Q2", "2026-Q3"], None, 2),
        (["2025-Q4", "2026-Q1"], 4, 4),
    ],
)
async def test_detailed_unmatched_quarter_labels_fall_back_to_recent(
    connector, specific_quarters, num_periods, expected_n
):
    optimizer = FinancialDataOptimizer(company_financial_connector=connector)
    period = FinancialPeriodRequirement(
        period_type="quarterly", specific_quarters=specific_quarters, num_periods=num_periods
    )

    _, _, quarterly = await optimizer.fetch_optimized_data("MSFT", FinancialDataRequirement.DETAILED, period)

    connector.get_company_quarterly_financial_statements_recent.assert_called_once_with("MSFT", expected_n)
    assert quarterly == [ROW]


@pytest.mark.asyncio
async def test_detailed_matched_quarter_labels_do_not_fall_back(connector):
    connector.get_company_quarterly_financial_statements_by_quarters.return_value = [SimpleNamespace()]
    optimizer = FinancialDataOptimizer(company_financial_connector=connector)
    period = FinancialPeriodRequirement(period_type="quarterly", specific_quarters=["3/31/2026"])

    _, _, quarterly = await optimizer.fetch_optimized_data("MSFT", FinancialDataRequirement.DETAILED, period)

    connector.get_company_quarterly_financial_statements_recent.assert_not_called()
    assert quarterly == [ROW]


@pytest.mark.asyncio
async def test_summary_unmatched_quarter_label_falls_back_to_latest_10q(connector):
    optimizer = FinancialDataOptimizer(company_financial_connector=connector)
    period = FinancialPeriodRequirement(period_type="quarterly", specific_quarters=["2026-Q3"])

    statements = await optimizer._fetch_quarterly_summary("MSFT", period)

    connector.get_company_quarterly_financial_statements_recent.assert_called_once_with(
        "MSFT", 1, require_10q_filing=True
    )
    assert statements == [ROW]
