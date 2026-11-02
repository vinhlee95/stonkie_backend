"""Reasoning for calculation questions in the v2 answer call: model choice, options, event order. Fakes only."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ai_models.model_name import ModelName
from services.analysis_progress import AnalysisPhase, ThinkingProgress
from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2
from services.question_analyzer.context_builders.comparison_builder import CompanyComparisonData
from services.question_analyzer.handlers_v2 import (
    CHECKING_STATUS,
    CONTEXT_STATUS,
    REASONING_ANSWER_CONFIG,
    REASONING_ANSWER_MODEL,
    REASONING_AUTO_ROUTER_MODELS,
    REASONING_STATUS,
    CompanyGeneralHandlerV2,
    CompanySpecificFinanceHandlerV2,
    GeneralFinanceHandlerV2,
    answer_call,
    answer_events,
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


def _assert_status_order(events: list[dict], reasoning: bool, checking: str = CHECKING_STATUS) -> None:
    """Answers with the web_search tool hold a status (the reasoning status, or `checking` without reasoning),
    then the reasoning, "Writing your answer", the answer."""
    types = [e["type"] for e in events]
    statuses = {e["body"]: i for i, e in enumerate(events) if e["type"] == "thinking_status"}
    writing = next(i for body, i in statuses.items() if "writing your answer" in body.lower())
    holding, absent = (REASONING_STATUS, checking) if reasoning else (checking, REASONING_STATUS)
    assert statuses[holding] < types.index("reasoning")
    assert absent not in statuses
    assert max(i for i, t in enumerate(types) if t == "reasoning") < writing < types.index("answer")


def _writing(progress: ThinkingProgress):
    return lambda: progress.status("Writing your answer...", phase=AnalysisPhase.ANALYZE)


def test_answer_events_without_reasoning_starts_with_the_writing_status():
    progress = ThinkingProgress()

    events = list(answer_events(iter(_STREAM), {"include_usage": True}, progress, _writing(progress)))

    assert [e["type"] for e in events][:2] == ["thinking_status", "reasoning"]
    assert events[0]["body"] == "Writing your answer..."


def test_answer_events_with_reasoning_holds_a_status_then_writes_after_the_reasoning():
    progress = ThinkingProgress()
    options = {"reasoning": {"effort": "low"}, "include_usage": True}

    events = list(answer_events(iter(_STREAM), options, progress, _writing(progress)))

    assert [e["type"] for e in events] == [
        "thinking_status",
        "reasoning",
        "reasoning",
        "thinking_status",
        "answer",
        "answer",
    ]
    statuses = [e for e in events if e["type"] == "thinking_status"]
    assert [(e["body"], e["step"]) for e in statuses] == [(REASONING_STATUS, 1), ("Writing your answer...", 2)]


def test_answer_events_drops_the_writing_status_when_no_answer_text_arrives():
    progress = ThinkingProgress()
    options = {"reasoning": {"effort": "low"}}

    events = list(answer_events(iter(({"type": "reasoning", "text": "x"},)), options, progress, _writing(progress)))

    # No answer to introduce: the reasoning status is the last one, nothing claims an answer is being written.
    assert [(e["type"], e["body"]) for e in events] == [("thinking_status", REASONING_STATUS), ("reasoning", "x")]


def test_iter_answer_events_keeps_reasoning_before_answer_and_drops_other_dicts():
    events = list(iter_answer_events(_STREAM))

    assert events == [
        {"type": "reasoning", "body": "69.0 -> 67.2 is down. "},
        {"type": "reasoning", "body": "So margins fell."},
        {"type": "answer", "body": "Gross margin fell"},
        {"type": "answer", "body": " to 67.2%."},
    ]
    assert all(isinstance(e["body"], str) for e in events)


def test_iter_answer_events_yields_the_writing_status_after_reasoning_before_the_answer():
    status = {"type": "thinking_status", "body": "Writing your answer..."}

    events = list(iter_answer_events(_STREAM, status))

    assert [e["type"] for e in events] == ["reasoning", "reasoning", "thinking_status", "answer", "answer"]
    assert events[2] is status


def test_answer_call_turns_on_reasoning_only_for_calculation_questions_on_the_default_model():
    model, options = answer_call(ModelName.Fastest, True)
    assert model == REASONING_ANSWER_MODEL == ModelName.Auto
    assert options == {
        "reasoning": REASONING_ANSWER_CONFIG,
        "auto_router_models": list(REASONING_AUTO_ROUTER_MODELS),
        "include_usage": True,
    }

    assert answer_call(ModelName.Fastest, False) == (ModelName.Fastest, {"include_usage": True})
    # Auto (the client default) reasons like fastest; otherwise unrestricted openrouter/auto.
    assert answer_call(ModelName.Auto, True) == (model, options)
    assert answer_call(ModelName.Auto, False) == (ModelName.Auto, {"include_usage": True})
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
    _assert_status_order(events, reasoning=needs_calculation)
    assert events[types.index("model_used")]["body"] is agent.served_model_name


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
    _assert_status_order(events, reasoning=True)


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
    _assert_status_order(events, reasoning=expects_reasoning)


@pytest.mark.asyncio
@pytest.mark.parametrize("needs_calculation", [True, False])
@patch(f"{_HANDLERS}.MultiAgent")
async def test_company_general_orders_statuses_around_reasoning(mock_agent_cls, needs_calculation):
    agent = _agent()
    mock_agent_cls.return_value = agent
    company_connector = MagicMock()
    company_connector.get_by_ticker.return_value = SimpleNamespace(name="Microsoft", country="United States")
    handler = CompanyGeneralHandlerV2(company_connector=company_connector)
    handler._generate_related_questions = _no_related  # type: ignore[method-assign]
    routing = _routing(needs_calculation, QuestionType.COMPANY_GENERAL)

    events = [
        e
        async for e in handler.handle(
            ticker="MSFT",
            question="How much did Microsoft's headcount grow?",
            search_decision=routing.search_decision,
            use_url_context=False,
            preferred_model=ModelName.Auto,
            routing=routing,
        )
    ]

    assert ("reasoning" in agent.generate_content.call_args.kwargs) is needs_calculation
    _assert_status_order(events, reasoning=needs_calculation, checking=CONTEXT_STATUS)
