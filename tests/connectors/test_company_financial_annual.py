"""CompanyFinancialConnector.upsert_annual_statement against the local Postgres test DB (row lock + overwrite)."""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

import connectors.company_financial as company_financial_module
from connectors.company_financial import CompanyFinancialConnector
from core.annual_periods import AnnualPeriod
from core.financial_statement_type import FinancialStatementType
from models.company_financial_statement import CompanyFinancialStatement
from models.company_quarterly_financial_statement import CompanyQuarterlyFinancialStatement

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/stonkie_test",
)

INCOME = FinancialStatementType.INCOME_STATEMENT
CASH_FLOW = FinancialStatementType.CASH_FLOW


@pytest.fixture(scope="module")
def annual_engine():
    engine = create_engine(TEST_DATABASE_URL)
    CompanyFinancialStatement.__table__.create(engine, checkfirst=True)
    CompanyQuarterlyFinancialStatement.__table__.create(engine, checkfirst=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def connector(annual_engine, monkeypatch):
    session_local = sessionmaker(bind=annual_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(company_financial_module, "SessionLocal", session_local)
    try:
        yield CompanyFinancialConnector()
    finally:
        with annual_engine.begin() as connection:
            connection.execute(
                text(
                    "TRUNCATE TABLE company_financial_statement, company_quarterly_financial_statement RESTART IDENTITY"
                )
            )


def rows_by_year(connector, ticker):
    return {row.period_end_year: row for row in connector.get_company_financial_statements(ticker)}


def test_creates_row_with_upper_cased_symbol(connector):
    assert connector.upsert_annual_statement("aapl", INCOME, AnnualPeriod(2026, False, {"Total Revenue": 1})) is True

    row = rows_by_year(connector, "AAPL")[2026]
    assert row.is_ttm is False
    assert row.income_statement == {"Total Revenue": 1}


def test_overwrites_stale_fiscal_year_figures(connector):
    # AAPL 2025 in prod held FY2024 revenue; a re-crawl must replace it.
    connector.upsert_annual_statement("AAPL", INCOME, AnnualPeriod(2025, False, {"Total Revenue": 391035000}))

    connector.upsert_annual_statement("AAPL", INCOME, AnnualPeriod(2025, False, {"Total Revenue": 416161000}))

    assert rows_by_year(connector, "AAPL")[2025].income_statement == {"Total Revenue": 416161000}


def test_fiscal_year_flips_ttm_row_and_clears_other_ttm_statements(connector):
    connector.upsert_annual_statement("AAPL", INCOME, AnnualPeriod(2026, True, {"Total Revenue": 435617000}))
    connector.upsert_annual_statement("AAPL", CASH_FLOW, AnnualPeriod(2026, True, {"Free Cash Flow": 1}))

    connector.upsert_annual_statement("AAPL", INCOME, AnnualPeriod(2026, False, {"Total Revenue": 440000000}))

    row = rows_by_year(connector, "AAPL")[2026]
    assert row.is_ttm is False
    assert row.income_statement == {"Total Revenue": 440000000}
    assert row.cash_flow is None


def test_refuses_ttm_onto_fiscal_year_row(connector):
    connector.upsert_annual_statement("AAPL", INCOME, AnnualPeriod(2026, False, {"Total Revenue": 440000000}))

    assert connector.upsert_annual_statement("AAPL", INCOME, AnnualPeriod(2026, True, {"Total Revenue": 1})) is False

    row = rows_by_year(connector, "AAPL")[2026]
    assert row.is_ttm is False
    assert row.income_statement == {"Total Revenue": 440000000}


def test_statement_line_items_include_quarterly_and_annual_of_that_statement(connector, annual_engine):
    connector.upsert_annual_statement("AAPL", INCOME, AnnualPeriod(2025, False, {"Total Revenue": 416161000}))
    connector.upsert_annual_statement("AAPL", CASH_FLOW, AnnualPeriod(2025, False, {"Free Cash Flow": 1}))
    with annual_engine.begin() as connection:
        connection.execute(
            CompanyQuarterlyFinancialStatement.__table__.insert(),
            [{"company_symbol": "AAPL", "period_end_quarter": "6/30/2026", "income_statement": {"Total Revenue": 94}}],
        )

    assert sorted(connector.get_statement_line_items("aapl", INCOME), key=str) == [
        {"Total Revenue": 416161000},
        {"Total Revenue": 94},
    ]


class _RacingSession(Session):
    """Another crawl inserts the same (symbol, year) row just before this session's first commit."""

    raced = 0
    races_left = 1

    def commit(self):
        if _RacingSession.races_left:
            _RacingSession.races_left -= 1
            _RacingSession.raced += 1
            with self.get_bind().begin() as connection:
                connection.execute(
                    CompanyFinancialStatement.__table__.insert(),
                    [{"company_symbol": "AAPL", "period_end_year": 2025, "is_ttm": True, "cash_flow": {"x": 1}}],
                )
        super().commit()


@pytest.fixture()
def racing_connector(connector, annual_engine, monkeypatch):
    _RacingSession.raced, _RacingSession.races_left = 0, 1
    monkeypatch.setattr(
        company_financial_module,
        "SessionLocal",
        sessionmaker(bind=annual_engine, class_=_RacingSession, autocommit=False, autoflush=False),
    )
    return connector


def test_concurrent_insert_is_retried_and_updates_the_new_row(racing_connector):
    assert racing_connector.upsert_annual_statement("AAPL", INCOME, AnnualPeriod(2025, False, {"Total Revenue": 2}))

    row = rows_by_year(racing_connector, "AAPL")[2025]
    assert _RacingSession.raced == 1
    assert row.is_ttm is False
    assert row.income_statement == {"Total Revenue": 2}
    assert row.cash_flow is None


def test_integrity_error_is_raised_when_retries_are_exhausted(racing_connector):
    with pytest.raises(IntegrityError):
        racing_connector.upsert_annual_statement(
            "AAPL", INCOME, AnnualPeriod(2025, False, {"Total Revenue": 2}), max_retries=1
        )
