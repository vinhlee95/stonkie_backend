"""Annual crawl save: TTM labeling and overwrite of stale rows (AAPL FY2025 / MSFT TTM-as-FY regressions)."""

from core.annual_periods import AnnualPeriod, plan_annual_row_update
from core.financial_statement_type import FinancialStatementType
from tasks.financial_crawler import _save_to_database


class InMemoryAnnualConnector:
    """Fake repository applying the same row-update rules as CompanyFinancialConnector."""

    def __init__(self, rows: dict[int, dict] | None = None, quarterly: list[dict] | None = None):
        self.rows = rows or {}
        self.quarterly = quarterly or []

    def get_statement_line_items(self, ticker, statement: FinancialStatementType) -> list[dict]:
        annual = [row[statement.value] for row in self.rows.values() if isinstance(row.get(statement.value), dict)]
        return [*self.quarterly, *annual]

    def upsert_annual_statement(self, ticker, statement: FinancialStatementType, period: AnnualPeriod) -> bool:
        row = self.rows.get(period.period_end_year)
        update = plan_annual_row_update(row["is_ttm"] if row else None, period, statement)
        if update is None:
            return False
        row = self.rows.setdefault(period.period_end_year, {})
        row["is_ttm"] = update.is_ttm
        row.update(update.fields)
        return True


def scraped(*periods):
    return [{"period_end_year": year, "metrics": {"Total Revenue": revenue}} for year, revenue in periods]


def test_recrawl_overwrites_stale_fiscal_year_and_moves_ttm():
    # AAPL in prod: FY2025 slot holds FY2024 figures, TTM from Feb 2026 sits in 2026.
    connector = InMemoryAnnualConnector(
        {
            2024: {"is_ttm": False, "income_statement": {"Total Revenue": 391035000}},
            2025: {"is_ttm": False, "income_statement": {"Total Revenue": 391035000}},
            2026: {"is_ttm": True, "income_statement": {"Total Revenue": 435617000}, "cash_flow": {"x": 1}},
        }
    )

    ok = _save_to_database(
        "AAPL",
        "income_statement",
        scraped(("TTM", 450000000), (2026, 440000000), (2025, 416161000), (2024, 391035000)),
        connector=connector,
    )

    assert ok is True
    assert connector.rows[2025]["income_statement"] == {"Total Revenue": 416161000}
    assert connector.rows[2026] == {
        "is_ttm": False,
        "income_statement": {"Total Revenue": 440000000},
        "cash_flow": None,
        "balance_sheet": None,
    }
    assert connector.rows[2027] == {"is_ttm": True, "income_statement": {"Total Revenue": 450000000}}


def test_ttm_never_overwrites_fiscal_year_row():
    connector = InMemoryAnnualConnector({2026: {"is_ttm": False, "income_statement": {"Total Revenue": 1}}})

    ok = _save_to_database("X", "income_statement", scraped(("TTM", 2), (2025, 3)), connector=connector)

    assert ok is True
    assert connector.rows[2026] == {"is_ttm": False, "income_statement": {"Total Revenue": 1}}


def test_full_unit_scrape_is_rescaled_to_stored_thousands():
    # The LLM sometimes returns Yahoo's figures in full units; stored rows are in thousands.
    connector = InMemoryAnnualConnector(
        {
            2023: {"is_ttm": False, "income_statement": {"Total Revenue": 383285000}},
            2024: {"is_ttm": False, "income_statement": {"Total Revenue": 391035000}},
        },
        quarterly=[{"Total Revenue": 94930000}, {"Total Revenue": 124300000}],
    )

    _save_to_database(
        "AAPL",
        "income_statement",
        scraped((2025, 416160000000), (2024, 391040000000), (2023, 383290000000)),
        connector=connector,
    )

    assert connector.rows[2025]["income_statement"] == {"Total Revenue": 416160000}
    assert connector.rows[2023]["income_statement"] == {"Total Revenue": 383290000}


def test_returns_false_when_connector_fails():
    class BrokenConnector(InMemoryAnnualConnector):
        def upsert_annual_statement(self, *args):
            raise RuntimeError("db down")

    assert _save_to_database("X", "cash_flow", scraped((2025, 1)), connector=BrokenConnector()) is False
