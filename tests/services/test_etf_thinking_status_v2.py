"""Exact ETF v2 thinking_status sequences, end to end through ETFAnalyzerV2 with the real ETF v2 handlers.

Same model as the stock sequences (tests/services/question_analyzer/test_thinking_status_v2.py): steps 1, 2, 3, ...
without total_steps, "Understanding your question..." first, the "Writing your answer" status right before the
first answer token. Fakes only: router, extractor, DB read, Brave retrieval and the LLMs.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.analyze_retrieval.schemas import BraveRetrievalError
from services.etf_analyzer_v2 import ETFAnalyzerV2
from services.etf_question_analyzer.comparison_handler_v2 import ETFComparisonHandlerV2
from services.etf_question_analyzer.handlers_v2 import (
    ETFDetailedAnalysisHandlerV2,
    ETFOverviewHandlerV2,
    GeneralETFHandlerV2,
)
from services.etf_question_analyzer.types import ETFDataRequirement, ETFQuestionType
from services.question_analyzer.router import ETFRoutingResult
from services.search_decision_engine import SearchDecision
from tests.services.etf_question_analyzer.etf_fakes import (
    FakeAgentFactory,
    FakeRetriever,
    make_etf,
    make_retrieval,
    make_source,
    no_related_questions,
)
from tests.services.routing_fakes import FakeTickerExtractor

_QUERY = "iShares Core S&P 500 UCITS ETF TER 2026"
UNDERSTANDING = ("Understanding your question...", "classify", 1)


class _Router:
    def __init__(self, result: ETFRoutingResult):
        self.result = result

    async def route_etf(self, **_kwargs) -> ETFRoutingResult:
        return self.result


class _Optimizer:
    def __init__(self, etf):
        self.etf = etf

    async def fetch_optimized_data(self, _ticker, data_requirement):
        return None if data_requirement == ETFDataRequirement.NONE else self.etf


class _Connector:
    def __init__(self, etfs: dict):
        self.etfs = etfs

    def get_by_ticker(self, ticker: str):
        return self.etfs.get(ticker)


def _routing(question_type: ETFQuestionType, *, search: bool) -> ETFRoutingResult:
    requirement = {
        ETFQuestionType.GENERAL_ETF: ETFDataRequirement.NONE,
        ETFQuestionType.ETF_OVERVIEW: ETFDataRequirement.BASIC,
        ETFQuestionType.ETF_DETAILED_ANALYSIS: ETFDataRequirement.DETAILED,
    }[question_type]
    return ETFRoutingResult(
        question_type=question_type,
        data_requirement=requirement,
        search_decision=SearchDecision(
            use_google_search=search,
            reason_code="latest_info" if search else "db_data_sufficient",
            confidence=0.9,
            decision_model="router:test",
            decision_fallback="none",
        ),
        search_queries=[_QUERY] if search else [],
        standalone_question="What is the TER of the iShares Core S&P 500 UCITS ETF?",
    )


def _analyzer(
    routing: ETFRoutingResult,
    *,
    etf=None,
    retriever: FakeRetriever | None = None,
    extractor_tickers: list[str] | None = None,
    comparison_etfs: dict | None = None,
) -> ETFAnalyzerV2:
    retriever = retriever or FakeRetriever()
    handler_kwargs: dict[str, Any] = {
        "retriever": retriever,
        "brave_client": object(),
        "agent_factory": FakeAgentFactory(),
        "related_questions": no_related_questions,
    }
    return ETFAnalyzerV2(
        router=_Router(routing),
        ticker_extractor=FakeTickerExtractor(extractor_tickers or []),
        data_optimizer=_Optimizer(etf if etf is not None else make_etf()),
        general_handler=GeneralETFHandlerV2(**handler_kwargs),
        overview_handler=ETFOverviewHandlerV2(**handler_kwargs),
        detailed_handler=ETFDetailedAnalysisHandlerV2(**handler_kwargs),
        comparison_handler=ETFComparisonHandlerV2(connector=_Connector(comparison_etfs or {}), **handler_kwargs),
    )


async def _sequence(analyzer: ETFAnalyzerV2, question: str = "What is the TER?") -> list[tuple[str, str, int]]:
    events = [e async for e in analyzer.analyze_question("SXR8", question)]
    statuses = [e for e in events if e["type"] == "thinking_status"]
    assert all("total_steps" not in e for e in statuses)
    types = [e["type"] for e in events]
    first_answer = types.index("answer")
    assert "writing your answer" in events[first_answer - 1]["body"].lower()
    assert "thinking_status" not in types[first_answer:]
    return [(e["body"], e["phase"], e["step"]) for e in statuses]


@pytest.mark.asyncio
async def test_overview_search_on():
    analyzer = _analyzer(_routing(ETFQuestionType.ETF_OVERVIEW, search=True))
    assert await _sequence(analyzer) == [
        UNDERSTANDING,
        (f'Searching for "{_QUERY}"...', "search", 2),
        ("Writing your answer from 1 source: justETF...", "analyze", 3),
    ]


@pytest.mark.asyncio
async def test_overview_search_off_with_complete_data():
    analyzer = _analyzer(_routing(ETFQuestionType.ETF_OVERVIEW, search=False))
    assert await _sequence(analyzer) == [UNDERSTANDING, ("Writing your answer...", "analyze", 2)]


@pytest.mark.asyncio
async def test_overview_missing_data_forces_search():
    analyzer = _analyzer(_routing(ETFQuestionType.ETF_OVERVIEW, search=False), etf=make_etf(ter_percent=None))
    sequence = await _sequence(analyzer)
    assert [s[1:] for s in sequence] == [("classify", 1), ("search", 2), ("analyze", 3)]
    assert sequence[1][0].startswith('Searching for "iShares Core S&P 500 UCITS ETF SXR8 ')
    assert sequence[2][0] == "Writing your answer from 1 source: justETF..."


@pytest.mark.asyncio
async def test_detailed_forced_search_failure_answers_from_the_database():
    analyzer = _analyzer(
        _routing(ETFQuestionType.ETF_DETAILED_ANALYSIS, search=False),
        etf=make_etf(holdings=[]),
        retriever=FakeRetriever(result=BraveRetrievalError("down")),
    )
    sequence = await _sequence(analyzer, "Top holdings?")
    assert sequence[0] == UNDERSTANDING
    assert sequence[1][1:] == ("search", 2)
    assert sequence[2] == ("No web results for SXR8, writing your answer...", "analyze", 3)
    assert len(sequence) == 3


@pytest.mark.asyncio
async def test_detailed_search_off_with_full_data():
    analyzer = _analyzer(_routing(ETFQuestionType.ETF_DETAILED_ANALYSIS, search=False))
    assert await _sequence(analyzer, "Top holdings?") == [UNDERSTANDING, ("Writing your answer...", "analyze", 2)]


@pytest.mark.asyncio
async def test_general_search_on_and_off():
    on = _analyzer(_routing(ETFQuestionType.GENERAL_ETF, search=True))
    assert await _sequence(on, "What is a TER?") == [
        UNDERSTANDING,
        (f'Searching for "{_QUERY}"...', "search", 2),
        ("Writing your answer from 1 source: justETF...", "analyze", 3),
    ]
    off = _analyzer(_routing(ETFQuestionType.GENERAL_ETF, search=False))
    assert await _sequence(off, "What is a TER?") == [UNDERSTANDING, ("Writing your answer...", "analyze", 2)]


_COMPARISON_ETFS = {"SXR8": make_etf("SXR8"), "VUSA": make_etf("VUSA", name="Vanguard S&P 500 UCITS ETF")}


@pytest.mark.asyncio
async def test_comparison_search_on():
    retriever = FakeRetriever(
        by_company={
            etf.name: make_retrieval(make_source(f"{t}_1", url=f"https://www.justetf.com/{t}"))
            for t, etf in _COMPARISON_ETFS.items()
        }
    )
    analyzer = _analyzer(
        _routing(ETFQuestionType.ETF_OVERVIEW, search=True),
        retriever=retriever,
        extractor_tickers=["SXR8", "VUSA"],
        comparison_etfs=_COMPARISON_ETFS,
    )
    assert await _sequence(analyzer, "SXR8 vs VUSA") == [
        UNDERSTANDING,
        ("Loading data for SXR8, VUSA...", "data_fetch", 2),
        ("Searching the web for SXR8, VUSA...", "search", 3),
        ("Writing your answer from 2 sources across SXR8, VUSA: justETF...", "analyze", 4),
    ]


@pytest.mark.asyncio
async def test_comparison_with_a_failed_ticker():
    retriever = FakeRetriever(
        by_company={
            _COMPARISON_ETFS["SXR8"].name: make_retrieval(make_source("SXR8_1")),
            _COMPARISON_ETFS["VUSA"].name: BraveRetrievalError("down"),
        }
    )
    analyzer = _analyzer(
        _routing(ETFQuestionType.ETF_OVERVIEW, search=True),
        retriever=retriever,
        extractor_tickers=["SXR8", "VUSA"],
        comparison_etfs=_COMPARISON_ETFS,
    )
    assert await _sequence(analyzer, "SXR8 vs VUSA") == [
        UNDERSTANDING,
        ("Loading data for SXR8, VUSA...", "data_fetch", 2),
        ("Searching the web for SXR8, VUSA...", "search", 3),
        ("No web results for VUSA, writing your answer from 1 source: justETF...", "analyze", 4),
    ]


@pytest.mark.asyncio
async def test_comparison_search_off():
    analyzer = _analyzer(
        _routing(ETFQuestionType.ETF_OVERVIEW, search=False),
        extractor_tickers=["SXR8", "VUSA"],
        comparison_etfs=_COMPARISON_ETFS,
    )
    assert await _sequence(analyzer, "SXR8 vs VUSA") == [
        UNDERSTANDING,
        ("Loading data for SXR8, VUSA...", "data_fetch", 2),
        ("Writing your answer...", "analyze", 3),
    ]
