"""v2 handlers with a RoutingResult from the single-call QuestionRouter (WP8). Fakes only."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_models.model_name import ModelName
from services.analyze_retrieval.query_reformulator import PrecomputedQueryReformulator
from services.analyze_retrieval.schemas import AnalyzeRetrievalResult, AnalyzeSource
from services.question_analyzer.context_builders.comparison_builder import CompanyComparisonData
from services.question_analyzer.router import RoutingResult
from services.question_analyzer.types import FinancialDataRequirement, FinancialPeriodRequirement, QuestionType
from services.search_decision_engine import SearchDecision

_HANDLERS = "services.question_analyzer.handlers_v2"


def _decision(use_search: bool = True) -> SearchDecision:
    return SearchDecision(
        use_google_search=use_search,
        reason_code="latest_info" if use_search else "db_data_sufficient",
        confidence=0.9,
        decision_model="router:test",
        decision_fallback="none",
    )


def _routing(data_requirement=FinancialDataRequirement.DETAILED, **overrides) -> RoutingResult:
    values = {
        "question_type": QuestionType.COMPANY_SPECIFIC_FINANCE.value,
        "comparison_tickers": [],
        "data_requirement": data_requirement,
        "period_requirement": FinancialPeriodRequirement(period_type="quarterly", num_periods=1),
        "relevant_statements": None,
        "search_decision": _decision(),
        "search_queries": ["Nvidia data center revenue Q3 2026", "Nvidia AMD data center"],
        "standalone_question": "What was Nvidia's data center revenue last quarter?",
    }
    values.update(overrides)
    return RoutingResult(**values)


def _retrieval_result() -> AnalyzeRetrievalResult:
    return AnalyzeRetrievalResult(
        sources=[
            AnalyzeSource(
                id="s_1",
                url="https://www.reuters.com/a",
                title="A",
                publisher="Reuters",
                published_at=None,
                is_trusted=True,
            )
        ],
        query="Nvidia data center revenue Q3 2026",
        market="GLOBAL",
        request_id="req",
    )


def _agent(chunks=("Alpha", " beta"), delay: float = 0.0) -> MagicMock:
    def stream(**_kwargs):
        for chunk in chunks:
            time.sleep(delay)
            yield chunk

    agent = MagicMock()
    agent.model_name = "test-model"
    agent.generate_content.side_effect = stream
    return agent


async def _no_related(*_a, **_k):
    if False:
        yield {}


def _specific_handler(*, fundamental=None, fetch_delay: float = 0.0):
    from services.question_analyzer.handlers_v2 import CompanySpecificFinanceHandlerV2

    classifier = MagicMock()
    classifier.classify_data_and_period_requirement = AsyncMock()
    optimizer = MagicMock()
    calls: list[str] = []

    async def fetch(**_kwargs):
        calls.append("fetch")
        time.sleep(fetch_delay)  # today's DB fetch is sync inside an async def
        return fundamental, [{"period_end_year": 2025}], []

    optimizer.fetch_optimized_data = fetch
    handler = CompanySpecificFinanceHandlerV2(
        company_connector=MagicMock(), classifier=classifier, data_optimizer=optimizer
    )
    handler._generate_related_questions = _no_related  # type: ignore[attr-defined]
    return handler, classifier, calls


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.retrieve_for_analyze")
@patch(f"{_HANDLERS}.MultiAgent")
async def test_company_specific_uses_router_plan_and_searches_during_db_fetch(mock_agent_cls, mock_retrieve):
    mock_agent_cls.return_value = _agent()
    order: list[str] = []

    def slow_retrieve(**kwargs):
        order.append("retrieve")
        time.sleep(0.3)
        return _retrieval_result()

    mock_retrieve.side_effect = slow_retrieve
    handler, classifier, fetch_calls = _specific_handler(fetch_delay=0.3)
    routing = _routing()

    start = time.perf_counter()
    events = [
        e
        async for e in handler.handle(
            ticker="NVDA",
            question="How does that compare?",
            search_decision=routing.search_decision,
            use_url_context=False,
            preferred_model=ModelName.Auto,
            request_id="req",
            routing=routing,
        )
    ]
    elapsed = time.perf_counter() - start

    classifier.classify_data_and_period_requirement.assert_not_called()
    assert fetch_calls == ["fetch"]
    assert elapsed < 0.5, f"DB fetch and Brave ran serially ({elapsed:.2f}s)"
    kwargs = mock_retrieve.call_args.kwargs
    assert kwargs["question"] == routing.standalone_question
    assert isinstance(kwargs["query_reformulator"], PrecomputedQueryReformulator)
    assert kwargs["query_reformulator"].reformulate("x", ticker="NVDA", company_name="NVDA").queries == list(
        routing.search_queries
    )
    statuses = [e["body"] for e in events if e["type"] == "thinking_status"]
    assert statuses[0] == 'Searching for "Nvidia data center revenue Q3 2026"...'
    assert not any("Figuring out" in s for s in statuses)
    assert [e["type"] for e in events].count("sources") == 1


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.retrieve_for_analyze")
@patch(f"{_HANDLERS}.MultiAgent")
async def test_company_specific_basic_searches_after_fetch_with_fundamentals_market(mock_agent_cls, mock_retrieve):
    mock_agent_cls.return_value = _agent()
    mock_retrieve.return_value = _retrieval_result()
    handler, _, _ = _specific_handler(fundamental={"Name": "FPT Corp", "Country": "Vietnam"})
    routing = _routing(FinancialDataRequirement.BASIC, period_requirement=None)

    events = [
        e
        async for e in handler.handle(
            ticker="FPT",
            question="P/E?",
            search_decision=routing.search_decision,
            use_url_context=False,
            routing=routing,
        )
    ]

    kwargs = mock_retrieve.call_args.kwargs
    assert kwargs["market"] == "VN"
    assert kwargs["company_name"] == "FPT Corp"
    statuses = [e["body"] for e in events if e["type"] == "thinking_status"]
    assert statuses.index('Searching for "Nvidia data center revenue Q3 2026"...') > statuses.index(
        "Analyzing FPT financials..."
    )


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.retrieve_for_analyze")
@patch(f"{_HANDLERS}.MultiAgent")
async def test_company_specific_search_off_never_retrieves(mock_agent_cls, mock_retrieve):
    mock_agent_cls.return_value = _agent()
    handler, classifier, _ = _specific_handler()
    routing = _routing(search_decision=_decision(False), search_queries=[])

    events = [
        e
        async for e in handler.handle(
            ticker="NVDA", question="q", search_decision=routing.search_decision, use_url_context=False, routing=routing
        )
    ]

    mock_retrieve.assert_not_called()
    classifier.classify_data_and_period_requirement.assert_not_called()
    assert not any(e["type"] == "sources" for e in events)


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.retrieve_for_analyze")
@patch(f"{_HANDLERS}.MultiAgent")
async def test_general_finance_routed_search_emits_status_before_retrieval(mock_agent_cls, mock_retrieve):
    from services.question_analyzer.handlers_v2 import GeneralFinanceHandlerV2

    mock_agent_cls.return_value = _agent()
    events: list[dict] = []

    def retrieve(**_kwargs):
        assert any(e["body"].startswith("Searching for") for e in events if e["type"] == "thinking_status")
        return _retrieval_result()

    mock_retrieve.side_effect = retrieve
    handler = GeneralFinanceHandlerV2()
    handler._generate_related_questions = _no_related  # type: ignore[attr-defined]
    routing = _routing(question_type=QuestionType.GENERAL_FINANCE.value, standalone_question="What moves rates?")

    async for e in handler.handle(
        question="and that?", search_decision=_decision(), use_url_context=False, routing=routing
    ):
        events.append(e)

    assert mock_retrieve.call_args.kwargs["question"] == "What moves rates?"
    assert mock_retrieve.call_args.kwargs["ticker"] is None


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.retrieve_for_analyze")
@patch(f"{_HANDLERS}.MultiAgent")
async def test_company_general_routed_search_uses_standalone_question(mock_agent_cls, mock_retrieve):
    from services.question_analyzer.handlers_v2 import CompanyGeneralHandlerV2

    mock_agent_cls.return_value = _agent()
    mock_retrieve.return_value = _retrieval_result()
    connector = MagicMock()
    connector.get_by_ticker.return_value = SimpleNamespace(name="NVIDIA Corp", country="United States")
    handler = CompanyGeneralHandlerV2(company_connector=connector)
    handler._generate_related_questions = _no_related  # type: ignore[attr-defined]
    routing = _routing(question_type=QuestionType.COMPANY_GENERAL.value, standalone_question="Latest Nvidia news?")

    events = [
        e
        async for e in handler.handle(
            ticker="NVDA", question="news?", search_decision=_decision(), use_url_context=False, routing=routing
        )
    ]

    kwargs = mock_retrieve.call_args.kwargs
    assert kwargs["question"] == "Latest Nvidia news?"
    assert kwargs["company_name"] == "NVIDIA Corp"
    assert 'Searching for "Nvidia data center revenue Q3 2026"...' in [e["body"] for e in events if "body" in e]


@pytest.mark.asyncio
@patch("services.question_analyzer.comparison_handler_v2.retrieve_for_analyze")
@patch("services.question_analyzer.comparison_handler_v2.MultiAgent")
async def test_comparison_follow_up_retrieves_and_prompts_with_standalone_question(mock_agent_cls, mock_retrieve):
    from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2

    mock_agent = _agent()
    mock_agent_cls.return_value = mock_agent
    mock_retrieve.side_effect = lambda **kw: _retrieval_result()
    handler = CompanyComparisonHandlerV2(company_connector=MagicMock(), financial_connector=MagicMock())

    async def fetch(tickers):
        return [CompanyComparisonData(ticker=t, data_source="training_data") for t in tickers]

    handler._fetch_companies_parallel = fetch  # type: ignore[method-assign]
    handler._generate_related_questions = _no_related  # type: ignore[attr-defined]
    standalone = "How does Nvidia's data center revenue last quarter compare with AMD's?"
    routing = _routing(question_type=QuestionType.COMPANY_COMPARISON.value, standalone_question=standalone)

    async for _ in handler.handle(
        tickers=["NVDA", "AMD"],
        question="How does that compare with AMD?",
        search_decision=_decision(),
        short_analysis=True,
        preferred_model=ModelName.Auto,
        conversation_messages=[{"role": "user", "content": "What was Nvidia's data center revenue last quarter?"}],
        routing=routing,
    ):
        pass

    assert [c.kwargs["question"] for c in mock_retrieve.call_args_list] == [standalone, standalone]
    assert standalone in mock_agent.generate_content.call_args.kwargs["prompt"]
