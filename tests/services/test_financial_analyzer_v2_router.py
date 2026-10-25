"""FinancialAnalyzerV2 with the single-call QuestionRouter (WP8). Fakes only: no LLM, DB or Brave calls."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.financial_analyzer_v2 import FinancialAnalyzerV2
from services.question_analyzer.router import QuestionRouter, RoutingResult
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


def _analyzer(router, extractor=None, classification="company-general"):
    classifier = MagicMock()
    classifier.classify_question_type = AsyncMock(return_value=(classification, None))
    engine = MagicMock()
    engine.decide = AsyncMock(
        return_value=SearchDecision(
            use_google_search=False,
            reason_code="legacy",
            confidence=1.0,
            decision_model="legacy",
            decision_fallback="none",
        )
    )
    connector = MagicMock()
    connector.get_available_periods.return_value = {"annual": [2025], "quarterly": []}
    connector.get_available_metrics.return_value = ["Revenue"]
    handlers = {name: _handler() for name in ("general", "finance", "specific", "comparison")}
    analyzer = FinancialAnalyzerV2(
        classifier=classifier,
        search_decision_engine=engine,
        company_financial_connector=connector,
        company_general_handler=handlers["general"],
        general_finance_handler=handlers["finance"],
        company_specific_finance_handler=handlers["specific"],
        comparison_handler=handlers["comparison"],
        router=router,
        ticker_extractor=extractor or FakeTickerExtractor(),
    )
    return analyzer, classifier, engine, handlers


async def _events(analyzer, **kwargs) -> list[dict]:
    params = {"ticker": "NVDA", "question": "What was the data center revenue last quarter?"}
    params.update(kwargs)
    return [e async for e in analyzer.analyze_question(**params)]


@pytest.mark.asyncio
async def test_router_result_drives_dispatch_without_legacy_calls():
    routing = _routing()
    router = FakeRouter(routing)
    analyzer, classifier, engine, handlers = _analyzer(router)
    history = [{"role": "user", "content": "Tell me about Nvidia"}, {"role": "assistant", "content": "Sure"}]

    events = await _events(analyzer, ticker="nvda", conversation_messages=history)

    classifier.classify_question_type.assert_not_called()
    engine.decide.assert_not_called()
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
    assert [e["body"] for e in events if e["type"] == "thinking_status"] == ["Understanding your question..."]


@pytest.mark.asyncio
async def test_router_failure_falls_back_to_legacy_calls():
    analyzer, classifier, engine, handlers = _analyzer(FakeRouter(None), classification="company-general")

    await _events(analyzer)

    classifier.classify_question_type.assert_awaited_once()
    engine.decide.assert_awaited_once()
    (call,) = handlers["general"].calls
    assert call["routing"] is None
    assert call["search_decision"].decision_model == "legacy"


@pytest.mark.asyncio
async def test_extractor_tickers_make_a_comparison_with_router_decision():
    routing = _routing(question_type=QuestionType.COMPANY_GENERAL.value)
    analyzer, classifier, engine, handlers = _analyzer(FakeRouter(routing), FakeTickerExtractor(["NVDA", "AMD"]))

    await _events(analyzer, question="How does that compare with AMD?")

    classifier.classify_question_type.assert_not_called()
    engine.decide.assert_not_called()
    (call,) = handlers["comparison"].calls
    assert call["tickers"] == ["NVDA", "AMD"]
    assert call["routing"] == routing
    assert call["search_decision"] == routing.search_decision


@pytest.mark.asyncio
async def test_router_comparison_without_extractor_tickers_uses_legacy_routing():
    routing = _routing(question_type=QuestionType.COMPANY_COMPARISON.value, comparison_tickers=["NVDA", "AMD"])
    analyzer, classifier, engine, handlers = _analyzer(FakeRouter(routing), FakeTickerExtractor([]))

    await _events(analyzer)

    classifier.classify_question_type.assert_awaited_once()
    engine.decide.assert_awaited_once()
    assert handlers["comparison"].calls == []
    assert handlers["general"].calls[0]["routing"] is None


@pytest.mark.asyncio
async def test_extractor_comparison_with_failed_router_skips_legacy_classifier():
    analyzer, classifier, engine, handlers = _analyzer(FakeRouter(None), FakeTickerExtractor(["NVDA", "AMD"]))

    await _events(analyzer, question="NVDA vs AMD")

    classifier.classify_question_type.assert_not_called()
    engine.decide.assert_awaited_once()
    assert handlers["comparison"].calls[0]["tickers"] == ["NVDA", "AMD"]


@pytest.mark.asyncio
async def test_static_fact_fast_path_overrides_router_search():
    analyzer, _, _, handlers = _analyzer(FakeRouter(_routing(question_type=QuestionType.COMPANY_GENERAL.value)))

    events = await _events(analyzer, question="Who founded Nvidia?")

    decision = handlers["general"].calls[0]["search_decision"]
    assert decision.use_google_search is False
    assert decision.reason_code == "stable_concept"
    assert handlers["general"].calls[0]["routing"].search_queries == []
    assert [e["body"] for e in events if e["type"] == "thinking_status"] == ["Understanding your question..."]


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

    analyzer, _, _, handlers = _analyzer(QuestionRouter(llm=slow_llm), BlockingExtractor())

    start = time.perf_counter()
    await _events(analyzer)
    elapsed = time.perf_counter() - start

    assert handlers["general"].calls[0]["routing"] is not None
    assert elapsed < 0.5, f"router and extractor ran serially ({elapsed:.2f}s)"
