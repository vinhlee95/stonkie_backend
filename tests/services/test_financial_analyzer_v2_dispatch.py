from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_models.model_name import ModelName
from services.financial_analyzer_v2 import FinancialAnalyzerV2
from services.question_analyzer.types import QuestionType
from services.search_decision_engine import SearchDecision


def _decision() -> SearchDecision:
    return SearchDecision(
        use_google_search=False,
        reason_code="stable",
        confidence=0.9,
        decision_model="test",
        decision_fallback="none",
    )


def _make_analyzer(*, classification: str | None, comparison_tickers: list[str] | None = None):
    classifier = MagicMock()
    classifier.classify_question_type = AsyncMock(return_value=(classification, comparison_tickers or []))
    sd_engine = MagicMock()
    sd_engine.decide = AsyncMock(return_value=_decision())

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
        classifier=classifier,
        search_decision_engine=sd_engine,
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
    assert h["company_specific"].handle.call_args.kwargs.get("available_metrics") == ["Revenue"]


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
async def test_unknown_classification_returns_error_answer():
    analyzer, h = _make_analyzer(classification=None)
    events = [
        e
        async for e in analyzer.analyze_question(
            ticker="AAPL",
            question="???",
            preferred_model=ModelName.Auto,
        )
    ]
    error_answers = [e for e in events if e["type"] == "answer" and "Unable" in e.get("body", "")]
    assert len(error_answers) == 1
    h["company_general"].handle.assert_not_called()
