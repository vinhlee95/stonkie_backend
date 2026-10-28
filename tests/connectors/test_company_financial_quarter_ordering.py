"""Quarterly reads return the newest quarters by date, not by the "M/D/YYYY" string."""

from types import SimpleNamespace

import pytest

import connectors.company_financial as company_financial_module
from connectors.company_financial import CompanyFinancialConnector
from models.company_financial_statement import CompanyFinancialStatement
from models.company_quarterly_financial_statement import CompanyQuarterlyFinancialStatement

# DB (lexical) order seen for AAPL/MSFT/KO on 2026-10-25.
LEXICAL_ORDER = ["9/30/2025", "9/30/2024", "6/30/2026", "6/30/2025", "3/31/2026", "12/31/2025"]
NEWEST_FIRST = ["6/30/2026", "3/31/2026", "12/31/2025", "9/30/2025", "6/30/2025", "9/30/2024"]


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *criteria):
        # Only the 10-Q filter narrows rows here; ticker/period filters are covered by the real DB.
        if any("filing_10q_url" in str(c) for c in criteria):
            self._rows = [r for r in self._rows if getattr(r, "filing_10q_url", None)]
        return self

    def distinct(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows_by_entity):
        self._rows_by_entity = rows_by_entity

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def query(self, *entities):
        return _FakeQuery(self._rows_by_entity[entities[0]])


@pytest.fixture()
def connector(monkeypatch):
    # Like MSFT: the fiscal-Q4 quarter (6/30/2026) has no 10-Q, it is reported in the 10-K.
    quarter_rows = [
        SimpleNamespace(
            period_end_quarter=q,
            filing_10q_url=None if q == "6/30/2026" else f"https://sec.gov/{q.replace('/', '-')}.htm",
        )
        for q in LEXICAL_ORDER
    ]
    rows_by_entity = {
        CompanyQuarterlyFinancialStatement: quarter_rows,
        CompanyQuarterlyFinancialStatement.period_end_quarter: [(q,) for q in LEXICAL_ORDER],
        CompanyQuarterlyFinancialStatement.filing_10q_url: quarter_rows,
        CompanyFinancialStatement.period_end_year: [(2024,), (2025,)],
    }
    monkeypatch.setattr(company_financial_module, "SessionLocal", lambda: _FakeSession(rows_by_entity))
    return CompanyFinancialConnector()


def test_recent_quarters_returns_newest_by_date(connector):
    rows = connector.get_company_quarterly_financial_statements_recent("AAPL", 3)

    assert [r.period_end_quarter for r in rows] == ["6/30/2026", "3/31/2026", "12/31/2025"]


def test_latest_quarter_is_newest_by_date(connector):
    rows = connector.get_company_quarterly_financial_statements_recent("AAPL", 1)

    assert [r.period_end_quarter for r in rows] == ["6/30/2026"]


def test_latest_quarter_with_10q_skips_quarter_without_filing(connector):
    rows = connector.get_company_quarterly_financial_statements_recent("MSFT", 1, require_10q_filing=True)

    assert [r.period_end_quarter for r in rows] == ["3/31/2026"]


def test_quarters_by_list_returned_newest_first(connector):
    rows = connector.get_company_quarterly_financial_statements_by_quarters("AAPL", LEXICAL_ORDER)

    assert [r.period_end_quarter for r in rows] == NEWEST_FIRST


def test_available_periods_quarterly_sorted_by_date(connector):
    periods = connector.get_available_periods("AAPL")

    assert periods["quarterly"] == NEWEST_FIRST
    assert periods["annual"] == [2025, 2024]


def test_quarterly_filings_sorted_by_date(connector):
    filings = connector.get_company_filings("AAPL", "quarterly")

    assert [f["period_end_quarter"] for f in filings] == NEWEST_FIRST[1:]
    assert filings[0]["period_end_year"] == 2026
