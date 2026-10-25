from core.annual_periods import AnnualPeriod, label_annual_periods, plan_annual_row_update
from core.financial_statement_type import FinancialStatementType

INCOME = FinancialStatementType.INCOME_STATEMENT
CASH_FLOW = FinancialStatementType.CASH_FLOW
BALANCE_SHEET = FinancialStatementType.BALANCE_SHEET


class TestLabelAnnualPeriods:
    def test_ttm_takes_slot_after_latest_fiscal_year(self):
        periods = label_annual_periods(
            [
                {"period_end_year": "TTM", "metrics": {"Total Revenue": 435617000}},
                {"period_end_year": 2025, "metrics": {"Total Revenue": 416161000}},
                {"period_end_year": 2024, "metrics": {"Total Revenue": 391035000}},
            ]
        )

        assert periods == [
            AnnualPeriod(2026, True, {"Total Revenue": 435617000}),
            AnnualPeriod(2025, False, {"Total Revenue": 416161000}),
            AnnualPeriod(2024, False, {"Total Revenue": 391035000}),
        ]

    def test_coerces_string_years_and_lowercase_ttm(self):
        periods = label_annual_periods(
            [
                {"period_end_year": "ttm", "metrics": {"a": 1}},
                {"period_end_year": "2025", "metrics": {"a": 2}},
            ]
        )

        assert periods == [AnnualPeriod(2026, True, {"a": 1}), AnnualPeriod(2025, False, {"a": 2})]

    def test_drops_ttm_without_any_fiscal_year(self):
        assert label_annual_periods([{"period_end_year": "TTM", "metrics": {"a": 1}}]) == []

    def test_drops_unparseable_years_and_empty_metrics(self):
        periods = label_annual_periods(
            [
                {"period_end_year": "FY?", "metrics": {"a": 1}},
                {"period_end_year": 2024, "metrics": {}},
                {"period_end_year": 2023, "metrics": None},
                {"period_end_year": 2022, "metrics": {"a": 3}},
            ]
        )

        assert periods == [AnnualPeriod(2022, False, {"a": 3})]

    def test_keeps_first_occurrence_of_a_duplicate_year(self):
        periods = label_annual_periods(
            [
                {"period_end_year": 2025, "metrics": {"a": "latest"}},
                {"period_end_year": 2025, "metrics": {"a": "older"}},
            ]
        )

        assert periods == [AnnualPeriod(2025, False, {"a": "latest"})]


class TestPlanAnnualRowUpdate:
    def test_new_row_gets_statement_and_flag(self):
        update = plan_annual_row_update(None, AnnualPeriod(2026, True, {"a": 1}), INCOME)

        assert update.is_ttm is True
        assert update.fields == {"income_statement": {"a": 1}}

    def test_overwrites_existing_fiscal_year_statement(self):
        update = plan_annual_row_update(False, AnnualPeriod(2025, False, {"a": 2}), INCOME)

        assert update.is_ttm is False
        assert update.fields == {"income_statement": {"a": 2}}

    def test_refreshes_existing_ttm_row(self):
        update = plan_annual_row_update(True, AnnualPeriod(2026, True, {"a": 3}), CASH_FLOW)

        assert update.is_ttm is True
        assert update.fields == {"cash_flow": {"a": 3}}

    def test_fiscal_year_replacing_ttm_row_clears_other_ttm_statements(self):
        update = plan_annual_row_update(True, AnnualPeriod(2025, False, {"a": 4}), BALANCE_SHEET)

        assert update.is_ttm is False
        assert update.fields == {"balance_sheet": {"a": 4}, "income_statement": None, "cash_flow": None}

    def test_never_overwrites_fiscal_year_row_with_ttm(self):
        assert plan_annual_row_update(False, AnnualPeriod(2026, True, {"a": 5}), INCOME) is None
