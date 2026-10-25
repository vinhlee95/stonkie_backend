from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ai_models.model_name import ModelName
from services.financial_analyzer_v2 import FinancialAnalyzerV2
from services.question_analyzer.types import QuestionType
from tests.services.routing_fakes import FakeRouter, FakeTickerExtractor, make_routing


def _make_analyzer(*, classification: str, comparison_tickers: list[str] | None = None):
    router = FakeRouter(make_routing(classification))
    fin_conn = MagicMock()
    fin_conn.get_available_periods.return_value = {}
    fin_conn.get_available_metrics.return_value = ["Revenue"]

    company_general = MagicMock()
    general_finance = MagicMock()
    company_specific = MagicMock()
    comparison = MagicMock()

    async def _gen():
        yield {"type": "answer", "body": "ok"}

    company_general.handle = MagicMock(return_value=_gen())
    general_finance.handle = MagicMock(return_value=_gen())
    company_specific.handle = MagicMock(return_value=_gen())
    comparison.handle = MagicMock(return_value=_gen())

    analyzer = FinancialAnalyzerV2(
        router=router,
        ticker_extractor=FakeTickerExtractor(comparison_tickers),
        company_financial_connector=fin_conn,
        company_general_handler=company_general,
        general_finance_handler=general_finance,
        company_specific_finance_handler=company_specific,
        comparison_handler=comparison,
    )
    return analyzer, {
        "company_general": company_general,
        "general_finance": general_finance,
        "company_specific": company_specific,
        "comparison": comparison,
    }


@pytest.mark.asyncio
async def test_dispatch_company_general():
    analyzer, h = _make_analyzer(classification=QuestionType.COMPANY_GENERAL.value)
    events = [
        e
        async for e in analyzer.analyze_question(
            ticker="AAPL",
            question="What is Apple?",
            preferred_model=ModelName.Auto,
        )
    ]
    assert any(e.get("type") == "answer" for e in events)
    h["company_general"].handle.assert_called_once()
    h["general_finance"].handle.assert_not_called()
    h["company_specific"].handle.assert_not_called()
    h["comparison"].handle.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_general_finance():
    analyzer, h = _make_analyzer(classification=QuestionType.GENERAL_FINANCE.value)
    events = [
        e
        async for e in analyzer.analyze_question(
            ticker="",
            question="What is P/E?",
            preferred_model=ModelName.Auto,
        )
    ]
    assert any(e.get("type") == "answer" for e in events)
    h["general_finance"].handle.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_company_specific_finance():
    analyzer, h = _make_analyzer(classification=QuestionType.COMPANY_SPECIFIC_FINANCE.value)
    events = [
        e
        async for e in analyzer.analyze_question(
            ticker="AAPL",
            question="Apple Q3 revenue?",
            preferred_model=ModelName.Auto,
        )
    ]
    assert any(e.get("type") == "answer" for e in events)
    h["company_specific"].handle.assert_called_once()
    routing = h["company_specific"].handle.call_args.kwargs["routing"]
    assert routing.question_type == QuestionType.COMPANY_SPECIFIC_FINANCE.value


@pytest.mark.asyncio
async def test_dispatch_comparison():
    analyzer, h = _make_analyzer(
        classification=QuestionType.COMPANY_COMPARISON.value,
        comparison_tickers=["AAPL", "MSFT"],
    )
    events = [
        e
        async for e in analyzer.analyze_question(
            ticker="AAPL",
            question="Compare AAPL vs MSFT",
            preferred_model=ModelName.Auto,
        )
    ]
    assert any(e.get("type") == "answer" for e in events)
    h["comparison"].handle.assert_called_once()


@pytest.mark.asyncio
async def test_unsupported_question_type_returns_error_answer():
    analyzer, h = _make_analyzer(classification="not-a-question-type")
    events = [
        e
        async for e in analyzer.analyze_question(
            ticker="AAPL",
            question="???",
            preferred_model=ModelName.Auto,
        )
    ]
    error_answers = [e for e in events if e["type"] == "answer" and "Unsupported question type" in e.get("body", "")]
    assert len(error_answers) == 1
    h["company_general"].handle.assert_not_called()
