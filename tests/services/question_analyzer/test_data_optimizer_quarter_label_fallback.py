"""Router quarter labels ("2026-Q3", "latest") never match the DB's "M/D/YYYY" keys (Q12).

The optimizer maps "YYYY-Qn" to stored rows by calendar quarter. "latest", or a quarter newer than
any stored row, gets the newest rows; an older quarter that isn't stored gets none (never a
substitute quarter the model could mislabel).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import services.question_analyzer.data_optimizer as data_optimizer_module
from services.question_analyzer.data_optimizer import FinancialDataOptimizer
from services.question_analyzer.types import FinancialDataRequirement, FinancialPeriodRequirement

# Newest first, as the connector returns them
STORED = ["6/30/2026", "3/31/2026", "12/31/2025", "9/30/2025", "6/30/2025", "3/31/2025", "9/30/2024"]


def _row(period_end_quarter):
    return SimpleNamespace(period_end_quarter=period_end_quarter, filing_10q_url="https://sec.gov/q.htm")


@pytest.fixture()
def connector(monkeypatch):
    conn = MagicMock()
    conn.get_company_quarterly_financial_statements_recent.side_effect = lambda _t, n, **_kw: [
        _row(q) for q in STORED[:n]
    ]
    conn.get_company_quarterly_financial_statements_by_quarters.side_effect = lambda _t, quarters: [
        _row(q) for q in STORED if q in quarters
    ]
    monkeypatch.setattr(
        data_optimizer_module.CompanyFinancialConnector,
        "to_dict",
        staticmethod(lambda row: {"period_end_quarter": row.period_end_quarter, "filing_10q_url": row.filing_10q_url}),
    )
    return conn


async def _detailed_quarters(connector, specific_quarters, num_periods=None):
    optimizer = FinancialDataOptimizer(company_financial_connector=connector)
    period = FinancialPeriodRequirement(
        period_type="quarterly", specific_quarters=specific_quarters, num_periods=num_periods
    )
    _, _, quarterly = await optimizer.fetch_optimized_data("MSFT", FinancialDataRequirement.DETAILED, period)
    return [row["period_end_quarter"] for row in quarterly]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("specific_quarters", "num_periods", "expected"),
    [
        # "latest" -> newest N
        (["latest"], 1, ["6/30/2026"]),
        (["latest"], 3, ["6/30/2026", "3/31/2026", "12/31/2025"]),
        # Calendar-quarter match, including older quarters (not the newest row)
        (["2026-Q2"], None, ["6/30/2026"]),
        (["2025-Q1"], None, ["3/31/2025"]),
        (["2024-Q3", "2025-Q3"], None, ["9/30/2025", "9/30/2024"]),
        # Newer than anything stored (router guessing the current quarter) -> newest N
        (["2026-Q3"], None, ["6/30/2026"]),
        (["2026-Q3", "2026-Q4"], None, ["6/30/2026", "3/31/2026"]),
        # Older quarter that isn't stored -> nothing, not a substitute
        (["2024-Q4"], None, []),
        (["2023-Q1"], None, []),
    ],
)
async def test_detailed_quarter_labels(connector, specific_quarters, num_periods, expected):
    assert await _detailed_quarters(connector, specific_quarters, num_periods) == expected
    connector.get_company_quarterly_financial_statements_by_quarters.assert_not_called()


@pytest.mark.asyncio
async def test_detailed_exact_period_keys_are_looked_up_as_is(connector):
    assert await _detailed_quarters(connector, ["3/31/2026"]) == ["3/31/2026"]
    connector.get_company_quarterly_financial_statements_recent.assert_not_called()


@pytest.mark.asyncio
async def test_detailed_no_stored_quarters_returns_empty(connector):
    connector.get_company_quarterly_financial_statements_recent.side_effect = lambda *_a, **_kw: []

    assert await _detailed_quarters(connector, ["2026-Q3"]) == []
    assert await _detailed_quarters(connector, ["latest"], 1) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("specific_quarters", "num_periods", "expected"),
    [
        (["2026-Q3"], None, ["6/30/2026"]),
        (["2026-Q3"], 2, ["6/30/2026", "3/31/2026"]),
        (["2025-Q3"], None, ["9/30/2025"]),
        (["2024-Q4"], None, []),
    ],
)
async def test_summary_quarter_labels_require_10q(connector, specific_quarters, num_periods, expected):
    optimizer = FinancialDataOptimizer(company_financial_connector=connector)
    period = FinancialPeriodRequirement(
        period_type="quarterly", specific_quarters=specific_quarters, num_periods=num_periods
    )

    statements = await optimizer._fetch_quarterly_summary("MSFT", period)

    assert [row["period_end_quarter"] for row in statements] == expected
    assert connector.get_company_quarterly_financial_statements_recent.call_args.kwargs == {"require_10q_filing": True}
