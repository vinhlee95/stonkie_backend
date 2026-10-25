"""Exact v2 thinking_status sequences, end to end through FinancialAnalyzerV2 with the real v2 handlers.

Fakes only: router, DB fetch, Brave retrieval, URL ingest and the answer LLM are all faked.
Every sequence must number its steps 1, 2, 3, ... without total_steps and end with the "Writing your answer"
status right before the first answer token.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from services.analyze_retrieval.query_reformulator import PrecomputedQueryReformulator
from services.analyze_retrieval.schemas import AnalyzeRetrievalResult, AnalyzeSource, BraveRetrievalError
from services.analyze_retrieval.url_ingest import UrlIngestResult
from services.financial_analyzer_v2 import FinancialAnalyzerV2
from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2
from services.question_analyzer.context_builders.comparison_builder import CompanyComparisonData
from services.question_analyzer.handlers_v2 import (
    CompanyGeneralHandlerV2,
    CompanySpecificFinanceHandlerV2,
    GeneralFinanceHandlerV2,
)
from services.question_analyzer.router import RoutingResult
from services.question_analyzer.types import FinancialDataRequirement, FinancialPeriodRequirement, QuestionType
from services.search_decision_engine import SearchDecision
from tests.services.routing_fakes import FakeRouter, FakeTickerExtractor

_HANDLERS = "services.question_analyzer.handlers_v2"
_COMPARISON = "services.question_analyzer.comparison_handler_v2"
_QUERY = "Apple stock buy or sell analysis October 2026"


def _decision(use_search: bool, reason_code: str | None = None) -> SearchDecision:
    return SearchDecision(
        use_google_search=use_search,
        reason_code=reason_code or ("latest_info" if use_search else "db_data_sufficient"),
        confidence=0.9,
        decision_model="test",
        decision_fallback="none",
    )


def _routing(
    question_type: QuestionType = QuestionType.COMPANY_SPECIFIC_FINANCE,
    *,
    use_search: bool = True,
    data_requirement: FinancialDataRequirement = FinancialDataRequirement.NONE,
    period_type: str | None = None,
) -> RoutingResult:
    return RoutingResult(
        question_type=question_type.value,
        comparison_tickers=[],
        data_requirement=data_requirement,
        period_requirement=FinancialPeriodRequirement(period_type=period_type) if period_type else None,
        relevant_statements=None,
        search_decision=_decision(use_search),
        search_queries=[_QUERY] if use_search else [],
        standalone_question="Should I buy or sell Apple stock now?",
    )


def _source(source_id: str, publisher: str, *, trusted: bool = True) -> AnalyzeSource:
    return AnalyzeSource(
        id=source_id,
        url=f"https://{publisher.lower().replace(' ', '')}.com/{source_id}",
        title=source_id,
        publisher=publisher,
        is_trusted=trusted,
    )


def _retrieval(*sources: AnalyzeSource) -> AnalyzeRetrievalResult:
    return AnalyzeRetrievalResult(
        sources=list(sources) or [_source("s_1", "Reuters")],
        query="q",
        market="GLOBAL",
        request_id="req",
    )


async def _no_related(*_args, **_kwargs):
    if False:
        yield {}


def _agent() -> MagicMock:
    agent = MagicMock()
    agent.model_name = "test-model"
    agent.generate_content.side_effect = lambda **_kwargs: iter(["Answer", " text"])
    return agent


def _optimizer(fundamental=None, annual=None, quarterly=None, *, block_seconds: float = 0.0) -> MagicMock:
    async def fetch(**_kwargs):
        time.sleep(block_seconds)  # today's DB fetch is sync inside an async def: it blocks the loop
        return fundamental, list(annual or []), list(quarterly or [])

    optimizer = MagicMock()
    optimizer.fetch_optimized_data = fetch
    return optimizer


def _analyzer(
    *,
    routing: RoutingResult | None,
    extractor_tickers: list[str] | None = None,
    optimizer: MagicMock | None = None,
    comparison_data: list[CompanyComparisonData] | None = None,
) -> FinancialAnalyzerV2:
    company_connector = MagicMock()
    company_connector.get_by_ticker.return_value = SimpleNamespace(name="Apple Inc.", country="United States")
    company_connector.get_fundamental_data.return_value = SimpleNamespace(name="Apple Inc.")
    financial_connector = MagicMock()
    financial_connector.get_available_periods.return_value = None
    financial_connector.get_available_metrics.return_value = None

    general = CompanyGeneralHandlerV2(company_connector=company_connector)
    finance = GeneralFinanceHandlerV2()
    specific = CompanySpecificFinanceHandlerV2(
        company_connector=company_connector,
        data_optimizer=optimizer or _optimizer(),
    )
    comparison = CompanyComparisonHandlerV2(company_connector=MagicMock(), financial_connector=MagicMock())

    async def fetch_companies(tickers):
        return comparison_data or [CompanyComparisonData(ticker=t, data_source="training_data") for t in tickers]

    comparison._fetch_companies_parallel = fetch_companies  # type: ignore[method-assign]
    for handler in (general, finance, specific, comparison):
        handler._generate_related_questions = _no_related  # type: ignore[method-assign]

    return FinancialAnalyzerV2(
        router=FakeRouter(routing),
        ticker_extractor=FakeTickerExtractor(extractor_tickers),
        company_connector=company_connector,
        company_financial_connector=financial_connector,
        company_general_handler=general,
        general_finance_handler=finance,
        company_specific_finance_handler=specific,
        comparison_handler=comparison,
    )


async def _run(analyzer: FinancialAnalyzerV2, **kwargs) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"ticker": "AAPL", "question": "Should I buy or sell Apple stock now?"}
    params.update(kwargs)
    return [event async for event in analyzer.analyze_question(**params)]


def _sequence(events: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(phase, body) per status, after checking the step model every client relies on."""
    statuses = [e for e in events if e["type"] == "thinking_status"]
    assert [s["step"] for s in statuses] == list(range(1, len(statuses) + 1))
    assert all("total_steps" not in s for s in statuses)
    answer_indexes = [i for i, e in enumerate(events) if e["type"] == "answer"]
    if answer_indexes:
        last_status_index = max(i for i, e in enumerate(events) if e["type"] == "thinking_status")
        assert last_status_index < answer_indexes[0]
    return [(str(s["phase"]), s["body"]) for s in statuses]


# --- CompanySpecificFinanceHandlerV2 -------------------------------------------------------------------


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.retrieve_for_analyze")
@patch(f"{_HANDLERS}.MultiAgent")
async def test_router_search_with_statements_reports_analysis_while_search_runs(mock_agent_cls, mock_retrieve):
    mock_agent_cls.return_value = _agent()

    def slow_retrieve(**_kwargs):
        time.sleep(0.2)
        return _retrieval(_source("s_1", "Reuters"), _source("s_2", "CNBC"), _source("s_3", "Blog", trusted=False))

    mock_retrieve.side_effect = slow_retrieve
    analyzer = _analyzer(
        routing=_routing(data_requirement=FinancialDataRequirement.DETAILED, period_type="both"),
        optimizer=_optimizer(annual=[{"period_end_year": 2025}], quarterly=[{"period_end_quarter": "2025-Q3"}]),
    )

    events = await _run(analyzer)

    # The DB fetch starts together with Brave, so only the search is announced (no "Loading ..." burst).
    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("search", f'Searching for "{_QUERY}"...'),
        ("analyze", "Analyzing AAPL financials..."),
        ("analyze", "Writing your answer from 3 sources: Reuters, CNBC..."),
    ]


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.retrieve_for_analyze")
@patch(f"{_HANDLERS}.MultiAgent")
async def test_router_search_finished_during_db_fetch_skips_analyzing(mock_agent_cls, mock_retrieve):
    mock_agent_cls.return_value = _agent()
    mock_retrieve.return_value = _retrieval()
    analyzer = _analyzer(
        routing=_routing(data_requirement=FinancialDataRequirement.DETAILED, period_type="annual"),
        optimizer=_optimizer(annual=[{"period_end_year": 2025}], block_seconds=0.2),
    )

    events = await _run(analyzer)

    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("search", f'Searching for "{_QUERY}"...'),
        ("analyze", "Writing your answer from 1 source: Reuters..."),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("period_type", "wording"),
    [("annual", "annual"), ("quarterly", "quarterly"), ("both", "annual and quarterly")],
)
@patch(f"{_HANDLERS}.retrieve_for_analyze")
@patch(f"{_HANDLERS}.MultiAgent")
async def test_router_search_off_loads_statements_with_human_period_wording(
    mock_agent_cls, mock_retrieve, period_type, wording
):
    mock_agent_cls.return_value = _agent()
    analyzer = _analyzer(
        routing=_routing(use_search=False, data_requirement=FinancialDataRequirement.DETAILED, period_type=period_type),
        optimizer=_optimizer(annual=[{"period_end_year": 2025}]),
    )

    events = await _run(analyzer)

    mock_retrieve.assert_not_called()
    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("data_fetch", f"Loading AAPL {wording} financial reports..."),
        ("analyze", "Writing your answer..."),
    ]


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.retrieve_for_analyze")
@patch(f"{_HANDLERS}.MultiAgent")
async def test_router_failure_default_routing_announces_the_question_as_the_query(mock_agent_cls, mock_retrieve):
    mock_agent_cls.return_value = _agent()
    seen_before_retrieve: list[str] = []
    events: list[dict[str, Any]] = []

    def retrieve(**kwargs):
        seen_before_retrieve.extend(e["body"] for e in events if e["type"] == "thinking_status")
        assert isinstance(kwargs["query_reformulator"], PrecomputedQueryReformulator)
        return _retrieval()

    mock_retrieve.side_effect = retrieve
    analyzer = _analyzer(routing=None)

    async for event in analyzer.analyze_question(ticker="AAPL", question="How is Apple doing?"):
        events.append(event)

    # default_routing: company-general, search on, the question itself is the query.
    searching = 'Searching for "How is Apple doing?"...'
    assert seen_before_retrieve[-1] == searching
    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("search", searching),
        ("analyze", "Writing your answer from 1 source: Reuters..."),
    ]


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.retrieve_for_analyze")
@patch(f"{_HANDLERS}.MultiAgent")
async def test_unresolved_router_comparison_is_downgraded_and_keeps_the_status_sequence(mock_agent_cls, mock_retrieve):
    mock_agent_cls.return_value = _agent()
    mock_retrieve.return_value = _retrieval()
    analyzer = _analyzer(routing=_routing(QuestionType.COMPANY_COMPARISON), extractor_tickers=[])

    events = await _run(analyzer, question="How does Apple compare with its peers?")

    # data NONE -> company-general with the router's query.
    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("search", f'Searching for "{_QUERY}"...'),
        ("analyze", "Writing your answer from 1 source: Reuters..."),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data_requirement", "statement", "period_wording", "form"),
    [
        (
            FinancialDataRequirement.QUARTERLY_SUMMARY,
            {"filing_10q_url": "https://sec.gov/msft-10q.htm", "period_end_quarter": "2026-Q1"},
            "quarterly",
            "10-Q",
        ),
        (
            FinancialDataRequirement.ANNUAL_SUMMARY,
            {"filing_10k_url": "https://sec.gov/msft-10k.htm", "period_end_year": "2026"},
            "annual",
            "10-K",
        ),
    ],
)
@patch(f"{_HANDLERS}.ingest_url")
@patch(f"{_HANDLERS}.retrieve_for_analyze")
@patch(f"{_HANDLERS}.MultiAgent")
async def test_single_filing_is_read_instead_of_searching(
    mock_agent_cls, mock_retrieve, mock_ingest, data_requirement, statement, period_wording, form
):
    mock_agent_cls.return_value = _agent()
    mock_ingest.return_value = UrlIngestResult(source=_source("filing", "SEC"), selected_passages=[])
    is_quarterly = data_requirement == FinancialDataRequirement.QUARTERLY_SUMMARY
    analyzer = _analyzer(
        routing=_routing(data_requirement=data_requirement, period_type=period_wording),
        optimizer=_optimizer(
            annual=None if is_quarterly else [statement],
            quarterly=[statement] if is_quarterly else None,
        ),
    )

    events = await _run(analyzer, ticker="MSFT", question="How did Microsoft's latest quarter go?")

    mock_retrieve.assert_not_called()
    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("data_fetch", f"Loading MSFT {period_wording} financial reports..."),
        ("search", f"Reading the MSFT {form} filing..."),
        ("analyze", "Writing your answer..."),
    ]


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.retrieve_for_analyze")
@patch(f"{_HANDLERS}.MultiAgent")
async def test_data_requirement_none_with_search_claims_no_financials(mock_agent_cls, mock_retrieve):
    mock_agent_cls.return_value = _agent()

    def slow_retrieve(**_kwargs):
        time.sleep(0.1)
        return _retrieval()

    mock_retrieve.side_effect = slow_retrieve
    analyzer = _analyzer(routing=_routing(data_requirement=FinancialDataRequirement.NONE))

    events = await _run(analyzer, ticker="NVDA", question="What is the latest news on Nvidia?")

    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("search", f'Searching for "{_QUERY}"...'),
        ("analyze", "Writing your answer from 1 source: Reuters..."),
    ]


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.MultiAgent")
async def test_data_requirement_none_without_search(mock_agent_cls):
    mock_agent_cls.return_value = _agent()
    analyzer = _analyzer(routing=_routing(use_search=False, data_requirement=FinancialDataRequirement.NONE))

    events = await _run(analyzer)

    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("analyze", "Writing your answer..."),
    ]


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.MultiAgent")
async def test_fallback_1_missing_ticker_answers_from_conversation(mock_agent_cls):
    mock_agent_cls.return_value = _agent()
    analyzer = _analyzer(routing=_routing(use_search=False, data_requirement=FinancialDataRequirement.DETAILED))

    events = await _run(
        analyzer,
        ticker="",
        question="And the year before?",
        conversation_messages=[{"role": "user", "content": "What was Apple's revenue?"}],
    )

    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("analyze", "Writing your answer from our conversation..."),
    ]
    assert [e["type"] for e in events].count("answer") == 2


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.MultiAgent")
async def test_fallback_2_no_financials_answers_from_conversation(mock_agent_cls):
    mock_agent_cls.return_value = _agent()
    analyzer = _analyzer(
        routing=_routing(use_search=False, data_requirement=FinancialDataRequirement.DETAILED, period_type="quarterly"),
        optimizer=_optimizer(),
    )

    events = await _run(
        analyzer,
        question="And last quarter?",
        conversation_messages=[{"role": "user", "content": "What was Apple's revenue?"}],
    )

    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("data_fetch", "Loading AAPL quarterly financial reports..."),
        ("analyze", "No AAPL financials found, writing your answer from our conversation..."),
    ]
    assert [e["type"] for e in events].count("answer") == 2


# --- CompanyGeneralHandlerV2 / GeneralFinanceHandlerV2 ---------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("routed", [True, False])
@patch(f"{_HANDLERS}.retrieve_for_analyze")
@patch(f"{_HANDLERS}.MultiAgent")
async def test_company_general_searches_before_writing(mock_agent_cls, mock_retrieve, routed):
    mock_agent_cls.return_value = _agent()
    mock_retrieve.return_value = _retrieval(_source("s_1", "Reuters"), _source("s_2", "Reuters"))
    question = "What is the latest news on Apple?"
    analyzer = _analyzer(routing=_routing(QuestionType.COMPANY_GENERAL) if routed else None)  # None: default routing

    events = await _run(analyzer, question=question)

    query = _QUERY if routed else question
    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("search", f'Searching for "{query}"...'),
        ("analyze", "Writing your answer from 2 sources: Reuters..."),
    ]


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.MultiAgent")
async def test_company_general_without_search(mock_agent_cls):
    mock_agent_cls.return_value = _agent()
    analyzer = _analyzer(routing=_routing(QuestionType.COMPANY_GENERAL, use_search=False))

    events = await _run(analyzer, question="Who founded Apple?")

    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("analyze", "Writing your answer..."),
    ]


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.ingest_url")
@patch(f"{_HANDLERS}.MultiAgent")
async def test_url_context_reads_the_document_once(mock_agent_cls, mock_ingest):
    mock_agent_cls.return_value = _agent()
    mock_ingest.return_value = UrlIngestResult(source=_source("doc", "Example"), selected_passages=[])
    analyzer = _analyzer(routing=_routing(QuestionType.COMPANY_GENERAL, use_search=False))

    events = await _run(analyzer, question="Summarize https://example.com/report", use_url_context=True)

    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("search", "Reading the attached document..."),
        ("analyze", "Writing your answer..."),
    ]


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.MultiAgent")
async def test_no_ticker_general_finance_makes_no_database_claim(mock_agent_cls):
    mock_agent_cls.return_value = _agent()
    analyzer = _analyzer(routing=_routing(QuestionType.GENERAL_FINANCE, use_search=False))

    events = await _run(analyzer, ticker="none", question="What is a P/E ratio?")

    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("analyze", "Writing your answer..."),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("routed", [True, False])
@patch(f"{_HANDLERS}.retrieve_for_analyze")
@patch(f"{_HANDLERS}.MultiAgent")
async def test_general_finance_with_search(mock_agent_cls, mock_retrieve, routed):
    mock_agent_cls.return_value = _agent()
    mock_retrieve.return_value = _retrieval(_source("s_1", "Blog", trusted=False))
    question = "What are interest rates doing?"
    analyzer = _analyzer(routing=_routing(QuestionType.GENERAL_FINANCE) if routed else None)  # None: default routing

    events = await _run(analyzer, ticker="none", question=question)

    query = _QUERY if routed else question
    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("search", f'Searching for "{query}"...'),
        ("analyze", "Writing your answer from 1 source..."),
    ]


# --- CompanyComparisonHandlerV2 -----------------------------------------------------------------------


@pytest.mark.asyncio
@patch(f"{_COMPARISON}.retrieve_for_analyze")
@patch(f"{_COMPARISON}.MultiAgent")
async def test_comparison_searches_all_tickers_then_writes(mock_agent_cls, mock_retrieve):
    mock_agent_cls.return_value = _agent()
    mock_retrieve.side_effect = lambda **kw: _retrieval(_source(f"{kw['ticker']}_1", "Reuters"))
    analyzer = _analyzer(routing=_routing(QuestionType.COMPANY_GENERAL), extractor_tickers=["NVDA", "AMD"])

    events = await _run(analyzer, ticker="NVDA", question="How does that compare with AMD?")

    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("data_fetch", "Loading financial data for NVDA, AMD..."),
        ("search", "Searching the web for NVDA, AMD..."),
        ("analyze", "Writing your answer from 2 sources across NVDA, AMD: Reuters..."),
    ]


@pytest.mark.asyncio
@patch(f"{_COMPARISON}.retrieve_for_analyze")
@patch(f"{_COMPARISON}.MultiAgent")
async def test_comparison_with_a_failed_ticker_folds_the_failure_into_the_final_status(mock_agent_cls, mock_retrieve):
    mock_agent_cls.return_value = _agent()

    def retrieve(**kwargs):
        if kwargs["ticker"] == "AMD":
            raise BraveRetrievalError("brave 500")
        return _retrieval(_source("nvda_1", "Reuters"), _source("nvda_2", "CNBC"))

    mock_retrieve.side_effect = retrieve
    analyzer = _analyzer(routing=_routing(QuestionType.COMPANY_GENERAL), extractor_tickers=["NVDA", "AMD"])

    events = await _run(analyzer, ticker="NVDA", question="How does that compare with AMD?")

    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("data_fetch", "Loading financial data for NVDA, AMD..."),
        ("search", "Searching the web for NVDA, AMD..."),
        ("analyze", "No web results for AMD, writing your answer from 2 sources: Reuters, CNBC..."),
    ]


@pytest.mark.asyncio
@patch(f"{_COMPARISON}.retrieve_for_analyze")
@patch(f"{_COMPARISON}.MultiAgent")
async def test_comparison_without_search(mock_agent_cls, mock_retrieve):
    mock_agent_cls.return_value = _agent()
    analyzer = _analyzer(
        routing=_routing(QuestionType.COMPANY_GENERAL, use_search=False),
        extractor_tickers=["NVDA", "AMD"],
        comparison_data=[
            CompanyComparisonData(ticker="NVDA", data_source="database"),
            CompanyComparisonData(ticker="AMD", data_source="database"),
        ],
    )

    events = await _run(analyzer, ticker="NVDA", question="NVDA vs AMD margins")

    mock_retrieve.assert_not_called()
    assert _sequence(events) == [
        ("classify", "Understanding your question..."),
        ("data_fetch", "Loading financial data for NVDA, AMD..."),
        ("analyze", "Writing your answer..."),
    ]


# --- analyzer-only paths -------------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("services.financial_analyzer_v2.validate_pdf_url", return_value=(True, None))
@patch("services.financial_analyzer_v2.MultiAgent")
async def test_pdf_url_sends_a_single_reading_status(mock_agent_cls, _mock_validate):
    agent = MagicMock()
    agent.model_name = "test-model"
    agent.generate_content_with_pdf_url.return_value = iter(["Answer"])
    mock_agent_cls.return_value = agent
    router = FakeRouter(None)
    analyzer = _analyzer(routing=None)
    analyzer.router = router

    events = await _run(analyzer, question="Summarize https://example.com/report.pdf")

    assert router.calls == []
    assert _sequence(events) == [("analyze", "Reading the attached document...")]
