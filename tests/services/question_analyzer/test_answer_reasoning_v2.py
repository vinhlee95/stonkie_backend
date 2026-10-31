"""Reasoning for calculation questions in the v2 answer call: model choice, options, event order. Fakes only."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ai_models.model_name import ModelName
from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2
from services.question_analyzer.context_builders.comparison_builder import CompanyComparisonData
from services.question_analyzer.handlers_v2 import (
    REASONING_ANSWER_CONFIG,
    REASONING_ANSWER_MODEL,
    REASONING_AUTO_ROUTER_MODELS,
    CompanySpecificFinanceHandlerV2,
    GeneralFinanceHandlerV2,
    answer_call,
    iter_answer_events,
)
from services.question_analyzer.router import RoutingResult
from services.question_analyzer.types import FinancialDataRequirement, FinancialPeriodRequirement, QuestionType
from services.search_decision_engine import SearchDecision

_HANDLERS = "services.question_analyzer.handlers_v2"
_COMPARISON = "services.question_analyzer.comparison_handler_v2"

_STREAM = (
    {"type": "reasoning", "text": "69.0 -> 67.2 is down. "},
    {"type": "url_citation", "url": "https://x"},
    {"type": "reasoning", "text": "So margins fell."},
    "Gross margin fell",
    {"type": "reasoning", "text": "late reasoning"},
    " to 67.2%.",
)


def _decision() -> SearchDecision:
    return SearchDecision(
        use_google_search=False,
        reason_code="db_data_sufficient",
        confidence=0.9,
        decision_model="router:test",
        decision_fallback="none",
    )


def _routing(needs_calculation: bool, question_type=QuestionType.COMPANY_SPECIFIC_FINANCE) -> RoutingResult:
    return RoutingResult(
        question_type=question_type.value,
        comparison_tickers=[],
        data_requirement=FinancialDataRequirement.DETAILED,
        period_requirement=FinancialPeriodRequirement(period_type="quarterly", num_periods=4),
        relevant_statements=None,
        search_decision=_decision(),
        search_queries=[],
        standalone_question="What's the profit margin trend in recent quarters?",
        needs_calculation=needs_calculation,
    )


def _agent() -> MagicMock:
    agent = MagicMock()
    agent.model_name = "test-model"
    agent.generate_content.side_effect = lambda **_kwargs: iter(_STREAM)
    return agent


async def _no_related(*_a, **_k):
    if False:
        yield {}


def _specific_handler() -> CompanySpecificFinanceHandlerV2:
    optimizer = MagicMock()

    async def fetch(**_kwargs):
        return {"Name": "Microsoft"}, [], [{"period_end_quarter": "6/30/2026"}]

    optimizer.fetch_optimized_data = fetch
    handler = CompanySpecificFinanceHandlerV2(company_connector=MagicMock(), data_optimizer=optimizer)
    handler._generate_related_questions = _no_related  # type: ignore[method-assign]
    return handler


def _answer_flow(events: list[dict]) -> list[str]:
    return [e["type"] for e in events if e["type"] in ("reasoning", "answer")]


def test_iter_answer_events_keeps_reasoning_before_answer_and_drops_other_dicts():
    events = list(iter_answer_events(_STREAM))

    assert events == [
        {"type": "reasoning", "body": "69.0 -> 67.2 is down. "},
        {"type": "reasoning", "body": "So margins fell."},
        {"type": "answer", "body": "Gross margin fell"},
        {"type": "answer", "body": " to 67.2%."},
    ]
    assert all(isinstance(e["body"], str) for e in events)


def test_answer_call_turns_on_reasoning_only_for_calculation_questions_on_the_default_model():
    model, options = answer_call(ModelName.Fastest, True)
    assert model == REASONING_ANSWER_MODEL == ModelName.Auto
    assert options == {
        "reasoning": REASONING_ANSWER_CONFIG,
        "auto_router_models": list(REASONING_AUTO_ROUTER_MODELS),
        "include_usage": True,
    }

    assert answer_call(ModelName.Fastest, False) == (ModelName.Fastest, {"include_usage": True})
    # An explicitly chosen model is kept, without reasoning.
    assert answer_call(ModelName.Sonnet46, True) == (ModelName.Sonnet46, {"include_usage": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("needs_calculation", [True, False])
@patch(f"{_HANDLERS}.MultiAgent")
async def test_company_specific_passes_reasoning_from_the_router(mock_agent_cls, needs_calculation):
    agent = _agent()
    mock_agent_cls.return_value = agent
    routing = _routing(needs_calculation)

    events = [
        e
        async for e in _specific_handler().handle(
            ticker="MSFT",
            question="What's the profit margin trend in recent quarters?",
            search_decision=routing.search_decision,
            use_url_context=False,
            preferred_model=ModelName.Fastest,
            routing=routing,
        )
    ]

    kwargs = agent.generate_content.call_args.kwargs
    if needs_calculation:
        mock_agent_cls.assert_called_once_with(model_name=ModelName.Auto)
        assert kwargs["reasoning"] == {"effort": "low"}
        assert kwargs["auto_router_models"] == list(REASONING_AUTO_ROUTER_MODELS)
    else:
        mock_agent_cls.assert_called_once_with(model_name=ModelName.Fastest)
        assert "reasoning" not in kwargs
    assert kwargs["include_usage"] is True
    assert _answer_flow(events) == ["reasoning", "reasoning", "answer", "answer"]
    assert "".join(e["body"] for e in events if e["type"] == "answer") == "Gross margin fell to 67.2%."
    types = [e["type"] for e in events]
    assert types.index("model_used") > max(i for i, t in enumerate(types) if t == "answer")


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.MultiAgent")
async def test_general_finance_streams_reasoning_before_answer(mock_agent_cls):
    agent = _agent()
    mock_agent_cls.return_value = agent
    routing = _routing(True, QuestionType.GENERAL_FINANCE)
    handler = GeneralFinanceHandlerV2()
    handler._generate_related_questions = _no_related  # type: ignore[method-assign]

    events = [
        e
        async for e in handler.handle(
            question="How fast did S&P 500 earnings grow?",
            search_decision=routing.search_decision,
            use_url_context=False,
            preferred_model=ModelName.Fastest,
            routing=routing,
        )
    ]

    assert agent.generate_content.call_args.kwargs["reasoning"] == {"effort": "low"}
    assert _answer_flow(events) == ["reasoning", "reasoning", "answer", "answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("routing", "expects_reasoning"), [(_routing(True), True), (None, False)])
@patch(f"{_COMPARISON}.MultiAgent")
async def test_comparison_uses_the_router_flag_and_defaults_to_no_reasoning(mock_agent_cls, routing, expects_reasoning):
    agent = _agent()
    mock_agent_cls.return_value = agent
    handler = CompanyComparisonHandlerV2(company_connector=MagicMock(), financial_connector=MagicMock())

    async def fake_fetch(tickers):
        return [CompanyComparisonData(ticker=t, data_source="database") for t in tickers]

    handler._fetch_companies_parallel = fake_fetch  # type: ignore[method-assign]
    handler._generate_related_questions = _no_related  # type: ignore[method-assign]

    events = [
        e
        async for e in handler.handle(
            tickers=["AAPL", "MSFT"],
            question="Compare Apple and Microsoft margins",
            search_decision=_decision(),
            short_analysis=True,
            preferred_model=ModelName.Fastest,
            routing=routing,
        )
    ]

    assert ("reasoning" in agent.generate_content.call_args.kwargs) is expects_reasoning
    assert _answer_flow(events) == ["reasoning", "reasoning", "answer", "answer"]
