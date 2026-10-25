"""FinancialAnalyzerV2 with the single-call QuestionRouter (WP8). Fakes only: no LLM, DB or Brave calls."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from services.financial_analyzer_v2 import FinancialAnalyzerV2
from services.question_analyzer.router import QuestionRouter, RoutingResult, default_routing
from services.question_analyzer.types import FinancialDataRequirement, QuestionType
from services.search_decision_engine import SearchDecision
from tests.services.routing_fakes import FakeRouter, FakeTickerExtractor


def _routing(**overrides) -> RoutingResult:
    values: dict[str, Any] = {
        "question_type": QuestionType.COMPANY_SPECIFIC_FINANCE.value,
        "comparison_tickers": [],
        "data_requirement": FinancialDataRequirement.DETAILED,
        "period_requirement": None,
        "relevant_statements": None,
        "search_decision": SearchDecision(
            use_google_search=True,
            reason_code="latest_info",
            confidence=0.9,
            decision_model="router:test",
            decision_fallback="none",
        ),
        "search_queries": ["Nvidia data center revenue Q3 2026"],
        "standalone_question": "What was Nvidia's data center revenue last quarter?",
    }
    values.update(overrides)
    return RoutingResult(**values)


def _handler() -> MagicMock:
    handler = MagicMock()
    calls: list[dict[str, Any]] = []

    async def handle(**kwargs):
        calls.append(kwargs)
        yield {"type": "answer", "body": "ok"}

    handler.handle = handle
    handler.calls = calls
    return handler


def _analyzer(router, extractor=None):
    connector = MagicMock()
    connector.get_available_periods.return_value = {"annual": [2025], "quarterly": []}
    connector.get_available_metrics.return_value = ["Revenue"]
    handlers = {name: _handler() for name in ("general", "finance", "specific", "comparison")}
    analyzer = FinancialAnalyzerV2(
        company_financial_connector=connector,
        company_general_handler=handlers["general"],
        general_finance_handler=handlers["finance"],
        company_specific_finance_handler=handlers["specific"],
        comparison_handler=handlers["comparison"],
        router=router,
        ticker_extractor=extractor or FakeTickerExtractor(),
    )
    return analyzer, handlers


async def _events(analyzer, **kwargs) -> list[dict]:
    params = {"ticker": "NVDA", "question": "What was the data center revenue last quarter?"}
    params.update(kwargs)
    return [e async for e in analyzer.analyze_question(**params)]


@pytest.mark.asyncio
async def test_router_result_drives_dispatch():
    routing = _routing()
    router = FakeRouter(routing)
    analyzer, handlers = _analyzer(router)
    history = [{"role": "user", "content": "Tell me about Nvidia"}, {"role": "assistant", "content": "Sure"}]

    events = await _events(analyzer, ticker="nvda", conversation_messages=history)

    assert router.calls == [
        {
            "question": "What was the data center revenue last quarter?",
            "ticker": "NVDA",
            "conversation_messages": history,
            "available_periods": {"annual": [2025], "quarterly": []},
            "available_metrics": ["Revenue"],
        }
    ]
    (call,) = handlers["specific"].calls
    assert call["routing"] == routing
    assert call["search_decision"] == routing.search_decision
    assert events[0]["body"] == "Searching for the latest NVDA data..."


@pytest.mark.asyncio
async def test_router_failure_uses_default_routing():
    question = "What was the data center revenue last quarter?"
    analyzer, handlers = _analyzer(FakeRouter(None))

    events = await _events(analyzer, question=question)

    (call,) = handlers["general"].calls
    assert call["routing"] == default_routing(question=question, ticker="NVDA")
    assert call["search_decision"].use_google_search is True
    assert call["search_decision"].reason_code == "ambiguous_default_on"
    assert call["routing"].search_queries == [question]
    assert events[0]["body"] == "Searching for the latest NVDA data..."


@pytest.mark.asyncio
async def test_router_failure_without_ticker_routes_to_general_finance():
    analyzer, handlers = _analyzer(FakeRouter(None))

    await _events(analyzer, ticker="", question="What is a P/E ratio?")

    (call,) = handlers["finance"].calls
    assert call["routing"].question_type == QuestionType.GENERAL_FINANCE.value
    assert handlers["general"].calls == []


@pytest.mark.asyncio
async def test_router_failure_keeps_forced_search_for_sec_urls():
    analyzer, handlers = _analyzer(FakeRouter(None))

    await _events(analyzer, question="Who founded Nvidia? https://www.sec.gov/Archives/edgar/data/1045810/x.htm")

    decision = handlers["general"].calls[0]["search_decision"]
    assert decision.use_google_search is True
    assert decision.reason_code == "sec_url"


@pytest.mark.asyncio
async def test_extractor_tickers_make_a_comparison_with_router_decision():
    routing = _routing(question_type=QuestionType.COMPANY_GENERAL.value)
    analyzer, handlers = _analyzer(FakeRouter(routing), FakeTickerExtractor(["NVDA", "AMD"]))

    await _events(analyzer, question="How does that compare with AMD?")

    (call,) = handlers["comparison"].calls
    assert call["tickers"] == ["NVDA", "AMD"]
    assert call["routing"] == routing
    assert call["search_decision"] == routing.search_decision


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data_requirement", "handler", "expected_type"),
    [
        (FinancialDataRequirement.DETAILED, "specific", QuestionType.COMPANY_SPECIFIC_FINANCE.value),
        (FinancialDataRequirement.NONE, "general", QuestionType.COMPANY_GENERAL.value),
    ],
)
async def test_router_comparison_without_extractor_tickers_is_downgraded(data_requirement, handler, expected_type):
    routing = _routing(
        question_type=QuestionType.COMPANY_COMPARISON.value,
        comparison_tickers=["NVDA", "AMD"],
        data_requirement=data_requirement,
    )
    analyzer, handlers = _analyzer(FakeRouter(routing), FakeTickerExtractor([]))

    await _events(analyzer)

    assert handlers["comparison"].calls == []
    (call,) = handlers[handler].calls
    assert call["routing"].question_type == expected_type
    assert call["routing"].search_queries == routing.search_queries
    assert call["routing"].standalone_question == routing.standalone_question
    assert call["search_decision"] == routing.search_decision


@pytest.mark.asyncio
async def test_router_comparison_without_ticker_or_extractor_tickers_is_general_finance():
    routing = _routing(question_type=QuestionType.COMPANY_COMPARISON.value, comparison_tickers=["NVDA", "AMD"])
    analyzer, handlers = _analyzer(FakeRouter(routing), FakeTickerExtractor([]))

    await _events(analyzer, ticker="")

    assert handlers["comparison"].calls == []
    assert handlers["finance"].calls[0]["routing"].question_type == QuestionType.GENERAL_FINANCE.value


@pytest.mark.asyncio
async def test_extractor_comparison_with_failed_router_uses_default_routing():
    analyzer, handlers = _analyzer(FakeRouter(None), FakeTickerExtractor(["NVDA", "AMD"]))

    await _events(analyzer, question="NVDA vs AMD")

    (call,) = handlers["comparison"].calls
    assert call["tickers"] == ["NVDA", "AMD"]
    assert call["routing"].standalone_question == "NVDA vs AMD"
    assert call["search_decision"].reason_code == "ambiguous_default_on"


_VALID_ROUTER_OUTPUT = json.dumps(
    {
        "standalone_question": "What was Nvidia's data center revenue last quarter?",
        "question_type": "company-specific-finance",
        "comparison_tickers": [],
        "data_requirement": "detailed",
        "period_requirement": None,
        "relevant_statements": None,
        "use_web_search": True,
        "reason_code": "latest_info",
        "search_queries": ["Nvidia data center revenue"],
    }
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("llm_output", "handler"),
    [(_VALID_ROUTER_OUTPUT, "specific"), ("not json", "general")],
    ids=["router-ok", "router-fails"],
)
async def test_each_request_makes_exactly_one_routing_llm_call(llm_output, handler):
    prompts: list[str] = []

    def llm(prompt: str) -> str:
        prompts.append(prompt)
        return llm_output

    analyzer, handlers = _analyzer(QuestionRouter(llm=llm))

    await _events(analyzer)

    assert len(prompts) == 1
    assert len(handlers[handler].calls) == 1


@pytest.mark.asyncio
async def test_static_fact_fast_path_overrides_router_search():
    analyzer, handlers = _analyzer(FakeRouter(_routing(question_type=QuestionType.COMPANY_GENERAL.value)))

    events = await _events(analyzer, question="Who founded Nvidia?")

    decision = handlers["general"].calls[0]["search_decision"]
    assert decision.use_google_search is False
    assert decision.reason_code == "stable_concept"
    assert handlers["general"].calls[0]["routing"].search_queries == []
    assert events[0]["body"] == "Found NVDA data in our database"


@pytest.mark.asyncio
async def test_router_runs_concurrently_with_blocking_ticker_extractor():
    """The router's LLM call runs in a worker thread while the (sync, loop-blocking) extractor runs."""

    def slow_llm(_prompt: str) -> str:
        time.sleep(0.3)
        return json.dumps(
            {
                "standalone_question": "Compare Nvidia and AMD",
                "question_type": "company-general",
                "comparison_tickers": [],
                "data_requirement": "none",
                "period_requirement": None,
                "relevant_statements": None,
                "use_web_search": False,
                "reason_code": "stable_concept",
                "search_queries": [],
            }
        )

    class BlockingExtractor:
        async def extract_tickers(self, question, current_ticker=None):
            time.sleep(0.3)  # today's extractor calls the LLM synchronously
            return []

    analyzer, handlers = _analyzer(QuestionRouter(llm=slow_llm), BlockingExtractor())

    start = time.perf_counter()
    await _events(analyzer)
    elapsed = time.perf_counter() - start

    assert handlers["general"].calls[0]["routing"] is not None
    assert elapsed < 0.5, f"router and extractor ran serially ({elapsed:.2f}s)"
