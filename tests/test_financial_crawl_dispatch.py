"""Tests for financial crawl task dispatch when a new ticker is requested."""

from unittest.mock import MagicMock, call, patch

import pytest

from connectors.cache import TaskDispatchDecision
from core.financial_statement_type import FinancialStatementType
from services.company import PeriodType, get_company_financial_statements


@pytest.fixture
def mock_can_dispatch():
    """All tasks dispatchable (no prior state in Redis)."""
    with patch("services.company.can_dispatch_task") as m:
        m.return_value = TaskDispatchDecision(can_dispatch=True, reason="No existing task found")
        yield m


@pytest.fixture
def mock_set_task_state():
    with patch("services.company.set_task_state") as m:
        yield m


@pytest.fixture
def mock_crawl_annual():
    with patch("services.company.crawl_annual_financial_data_task") as m:
        m.delay.return_value = MagicMock(id="annual-task-id")
        yield m


@pytest.fixture
def mock_crawl_quarterly():
    with patch("services.company.crawl_quarterly_financial_data_task") as m:
        m.delay.return_value = MagicMock(id="quarterly-task-id")
        yield m


REPORT_TYPES = [rt.value for rt in FinancialStatementType.crawl_dispatch_order()]


class TestNewTickerDispatchesBothAnnualAndQuarterly:
    """When no data exists for a ticker, both annual and quarterly crawl tasks should be dispatched."""

    @patch("services.company.company_financial_connector")
    def test_dispatches_annual_tasks_for_all_report_types(
        self, mock_connector, mock_crawl_annual, mock_crawl_quarterly, mock_set_task_state, mock_can_dispatch
    ):
        mock_connector.get_company_financial_statements.return_value = []
        mock_connector.get_company_quarterly_financial_statements.return_value = []

        get_company_financial_statements("AAPL", period_type=None)

        annual_calls = mock_crawl_annual.delay.call_args_list
        assert len(annual_calls) == 3
        for rpt in REPORT_TYPES:
            assert call("AAPL", rpt) in annual_calls

    @patch("services.company.company_financial_connector")
    def test_dispatches_quarterly_tasks_for_all_report_types(
        self, mock_connector, mock_crawl_annual, mock_crawl_quarterly, mock_set_task_state, mock_can_dispatch
    ):
        mock_connector.get_company_financial_statements.return_value = []
        mock_connector.get_company_quarterly_financial_statements.return_value = []

        get_company_financial_statements("AAPL", period_type=None)

        quarterly_calls = mock_crawl_quarterly.delay.call_args_list
        assert len(quarterly_calls) == 3
        for rpt in REPORT_TYPES:
            assert call("AAPL", rpt) in quarterly_calls

    @patch("services.company.company_financial_connector")
    def test_sets_task_state_for_quarterly(
        self, mock_connector, mock_crawl_annual, mock_crawl_quarterly, mock_set_task_state, mock_can_dispatch
    ):
        mock_connector.get_company_financial_statements.return_value = []
        mock_connector.get_company_quarterly_financial_statements.return_value = []

        get_company_financial_statements("AAPL", period_type=None)

        quarterly_state_calls = [
            c for c in mock_set_task_state.call_args_list if c.kwargs.get("period_type") == "quarterly"
        ]
        assert len(quarterly_state_calls) == 3

    @patch("services.company.company_financial_connector")
    def test_respects_can_dispatch_for_quarterly(
        self, mock_connector, mock_crawl_annual, mock_crawl_quarterly, mock_set_task_state
    ):
        """If Redis says a quarterly task is already running, don't dispatch again."""
        mock_connector.get_company_financial_statements.return_value = []
        mock_connector.get_company_quarterly_financial_statements.return_value = []

        with patch("services.company.can_dispatch_task") as mock_cd:
            # Annual: dispatchable, Quarterly: already running
            def side_effect(ticker, rpt_type, period_type):
                if period_type == "quarterly":
                    return TaskDispatchDecision(can_dispatch=False, reason="Task already running", existing_task_id="x")
                return TaskDispatchDecision(can_dispatch=True, reason="No existing task found")

            mock_cd.side_effect = side_effect

            get_company_financial_statements("AAPL", period_type=None)

        assert mock_crawl_quarterly.delay.call_count == 0


class TestExplicitQuarterlyRequest:
    """When period_type=quarterly and no data, quarterly crawl tasks should be dispatched."""

    @patch("services.company.company_financial_connector")
    def test_dispatches_quarterly_when_explicitly_requested(
        self, mock_connector, mock_crawl_annual, mock_crawl_quarterly, mock_set_task_state, mock_can_dispatch
    ):
        mock_connector.get_company_quarterly_financial_statements.return_value = []

        get_company_financial_statements("AAPL", period_type=PeriodType.QUARTERLY)

        quarterly_calls = mock_crawl_quarterly.delay.call_args_list
        assert len(quarterly_calls) == 3
        for rpt in REPORT_TYPES:
            assert call("AAPL", rpt) in quarterly_calls

    @patch("services.company.company_financial_connector")
    def test_no_dispatch_when_quarterly_data_exists(
        self, mock_connector, mock_crawl_annual, mock_crawl_quarterly, mock_set_task_state, mock_can_dispatch
    ):
        mock_connector.get_company_quarterly_financial_statements.return_value = [MagicMock()]

        get_company_financial_statements("AAPL", period_type=PeriodType.QUARTERLY)

        assert mock_crawl_quarterly.delay.call_count == 0


class TestMixedState:
    """When annual data exists but quarterly does not, only quarterly tasks should dispatch."""

    @patch("services.company.company_financial_connector")
    def test_only_quarterly_dispatched_when_annual_exists(
        self, mock_connector, mock_crawl_annual, mock_crawl_quarterly, mock_set_task_state, mock_can_dispatch
    ):
        mock_connector.get_company_financial_statements.return_value = [MagicMock()]
        mock_connector.get_company_quarterly_financial_statements.return_value = []

        get_company_financial_statements("AAPL", period_type=None)

        # Annual should NOT be dispatched (data exists)
        assert mock_crawl_annual.delay.call_count == 0

        # Quarterly SHOULD be dispatched (no data)
        quarterly_calls = mock_crawl_quarterly.delay.call_args_list
        assert len(quarterly_calls) == 3
        for rpt in REPORT_TYPES:
            assert call("AAPL", rpt) in quarterly_calls

    @patch("services.company.company_financial_connector")
    def test_no_dispatch_when_both_exist(
        self, mock_connector, mock_crawl_annual, mock_crawl_quarterly, mock_set_task_state, mock_can_dispatch
    ):
        mock_connector.get_company_financial_statements.return_value = [MagicMock()]
        mock_connector.get_company_quarterly_financial_statements.return_value = [MagicMock()]

        get_company_financial_statements("AAPL", period_type=None)

        assert mock_crawl_annual.delay.call_count == 0
        assert mock_crawl_quarterly.delay.call_count == 0
