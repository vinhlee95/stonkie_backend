"""Quarterly summaries use the newest quarter that has a 10-Q."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import services.question_analyzer.data_optimizer as data_optimizer_module
from services.question_analyzer.data_optimizer import FinancialDataOptimizer
from services.question_analyzer.types import FinancialPeriodRequirement


@pytest.fixture()
def connector(monkeypatch):
    conn = MagicMock()
    conn.get_company_quarterly_financial_statements_recent.return_value = [SimpleNamespace()]
    monkeypatch.setattr(
        data_optimizer_module.CompanyFinancialConnector,
        "to_dict",
        staticmethod(lambda _row: {"period_end_quarter": "3/31/2026", "filing_10q_url": "https://sec.gov/q.htm"}),
    )
    return conn


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "period_requirement",
    [
        None,
        FinancialPeriodRequirement(period_type="quarterly", specific_quarters=["latest"], num_periods=1),
    ],
)
async def test_latest_quarter_summary_requires_10q(connector, period_requirement):
    optimizer = FinancialDataOptimizer(company_financial_connector=connector)

    statements = await optimizer._fetch_quarterly_summary("MSFT", period_requirement)

    connector.get_company_quarterly_financial_statements_recent.assert_called_once_with(
        "MSFT", 1, require_10q_filing=True
    )
    assert statements == [{"period_end_quarter": "3/31/2026", "filing_10q_url": "https://sec.gov/q.htm"}]
