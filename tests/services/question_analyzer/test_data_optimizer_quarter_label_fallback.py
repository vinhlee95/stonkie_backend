"""Router quarter labels ("2026-Q3", "latest") never match the DB's "M/D/YYYY" keys (Q12).

Only labels meaning "the current quarter" are resolved: "latest", or a quarter at/after the newest
stored row, gets the newest rows. Older labels get none, never a substitute quarter the model could
present under the requested label (users often mean fiscal quarters).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import services.question_analyzer.data_optimizer as data_optimizer_module
from services.question_analyzer.data_optimizer import FinancialDataOptimizer
from services.question_analyzer.types import FinancialDataRequirement, FinancialPeriodRequirement

URL = "https://sec.gov/q.htm"
# (period end, 10-Q url), newest first as the connector returns them; fiscal Q4 has only a 10-K
STORED = [("6/30/2026", URL), ("3/31/2026", URL), ("12/31/2025", None), ("9/30/2025", URL), ("6/30/2025", URL)]
# Newest quarter not yet filed on a 10-Q
STORED_Q4_NEWEST = STORED[2:]


def _rows(stored):
    return [SimpleNamespace(period_end_quarter=q, filing_10q_url=url) for q, url in stored]


@pytest.fixture()
def connector(monkeypatch):
    conn = MagicMock()
    conn.get_company_quarterly_financial_statements_recent.side_effect = lambda _t, n: _rows(STORED)[:n]
    conn.get_company_quarterly_financial_statements_by_quarters.side_effect = lambda _t, quarters: [
        row for row in _rows(STORED) if row.period_end_quarter in quarters
    ]
    monkeypatch.setattr(
        data_optimizer_module.CompanyFinancialConnector,
        "to_dict",
        staticmethod(lambda row: {"period_end_quarter": row.period_end_quarter, "filing_10q_url": row.filing_10q_url}),
    )
    return conn


def _period(specific_quarters, num_periods=None):
    return FinancialPeriodRequirement(
        period_type="quarterly", specific_quarters=specific_quarters, num_periods=num_periods
    )


async def _detailed_quarters(connector, specific_quarters, num_periods=None):
    optimizer = FinancialDataOptimizer(company_financial_connector=connector)
    _, _, quarterly = await optimizer.fetch_optimized_data(
        "MSFT", FinancialDataRequirement.DETAILED, _period(specific_quarters, num_periods)
    )
    return [row["period_end_quarter"] for row in quarterly]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("specific_quarters", "num_periods", "expected"),
    [
        # "latest" -> newest N (10-K-only quarters included: detailed data needs no filing)
        (["latest"], 1, ["6/30/2026"]),
        (["latest"], 3, ["6/30/2026", "3/31/2026", "12/31/2025"]),
        (["latest", "2025-Q1"], 1, ["6/30/2026"]),
        # Same calendar quarter as the newest stored row -> that row
        (["2026-Q2"], None, ["6/30/2026"]),
        # Newer than anything stored (router guessing the current quarter) -> newest N
        (["2026-Q3"], None, ["6/30/2026"]),
        (["2026-Q3"], 2, ["6/30/2026", "3/31/2026"]),
        (["2026-Q3", "2026-Q4"], None, ["6/30/2026", "3/31/2026"]),
        # Older quarters -> nothing, not a calendar match that may be a different fiscal quarter
        (["2026-Q1"], None, []),
        (["2025-Q1"], None, []),
        (["2024-Q3", "2025-Q3"], None, []),
        # Unparseable labels -> nothing
        (["Q3 2026"], None, []),
    ],
)
async def test_detailed_quarter_labels(connector, specific_quarters, num_periods, expected):
    assert await _detailed_quarters(connector, specific_quarters, num_periods) == expected
    connector.get_company_quarterly_financial_statements_by_quarters.assert_not_called()
    assert connector.get_company_quarterly_financial_statements_recent.call_args.kwargs == {}


@pytest.mark.asyncio
async def test_detailed_exact_period_keys_are_looked_up_as_is(connector):
    assert await _detailed_quarters(connector, ["3/31/2026"]) == ["3/31/2026"]
    connector.get_company_quarterly_financial_statements_recent.assert_not_called()


@pytest.mark.asyncio
async def test_detailed_no_stored_quarters_returns_empty(connector):
    connector.get_company_quarterly_financial_statements_recent.side_effect = lambda *_a: []

    assert await _detailed_quarters(connector, ["2026-Q3"]) == []
    assert await _detailed_quarters(connector, ["latest"], 1) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "specific_quarters", "num_periods", "expected"),
    [
        (STORED, ["2026-Q3"], None, ["6/30/2026"]),
        (STORED, ["2026-Q3"], 2, ["6/30/2026", "3/31/2026"]),
        (STORED, ["2026-Q2"], None, ["6/30/2026"]),
        (STORED, ["2025-Q3"], None, []),
        # Requested quarter is the newest stored one but has only a 10-K -> nothing, not the prior 10-Q
        (STORED_Q4_NEWEST, ["2025-Q4"], None, []),
        # Newer than the newest stored (10-K-only) row -> latest 10-Q, as for "latest"
        (STORED_Q4_NEWEST, ["2026-Q1"], None, ["9/30/2025"]),
    ],
)
async def test_summary_quarter_labels_use_10q_quarters(connector, stored, specific_quarters, num_periods, expected):
    connector.get_company_quarterly_financial_statements_recent.side_effect = lambda _t, n: _rows(stored)[:n]
    optimizer = FinancialDataOptimizer(company_financial_connector=connector)

    statements = await optimizer._fetch_quarterly_summary("MSFT", _period(specific_quarters, num_periods))

    assert [row["period_end_quarter"] for row in statements] == expected
