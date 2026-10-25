"""ETFAnalyzerV2: route_etf alongside ETFTickerExtractor, default routing, data fetch and dispatch.

Fakes only: no LLM, DB or Brave calls.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from services.etf_analyzer_v2 import ETFAnalyzerV2
from services.etf_question_analyzer.types import ETFDataRequirement, ETFQuestionType
from services.question_analyzer.router import ETFRoutingResult
from services.search_decision_engine import SearchDecision
from tests.services.etf_question_analyzer.etf_fakes import make_etf
from tests.services.routing_fakes import FakeTickerExtractor

UNDERSTANDING = {"type": "thinking_status", "body": "Understanding your question...", "phase": "classify", "step": 1}


def _routing(
    question_type: ETFQuestionType = ETFQuestionType.ETF_OVERVIEW,
    data_requirement: ETFDataRequirement = ETFDataRequirement.BASIC,
    *,
    search: bool = True,
) -> ETFRoutingResult:
    return ETFRoutingResult(
        question_type=question_type,
        data_requirement=data_requirement,
        search_decision=SearchDecision(
            use_google_search=search,
            reason_code="latest_info" if search else "db_data_sufficient",
            confidence=0.9,
            decision_model="router:test",
            decision_fallback="none",
        ),
        search_queries=["iShares Core S&P 500 UCITS ETF TER"] if search else [],
        standalone_question="What is the TER of the iShares Core S&P 500 UCITS ETF?",
    )


class FakeETFRouter:
    def __init__(self, result: ETFRoutingResult | None = None):
        self.result = result
        self.calls: list[dict[str, Any]] = []
        self.started = False

    async def route_etf(self, **kwargs) -> ETFRoutingResult | None:
        self.started = True
        self.calls.append(kwargs)
        await asyncio.sleep(0)
        return self.result


class FakeDataOptimizer:
    def __init__(self, etf=None):
        self.etf = etf if etf is not None else make_etf()
        self.calls: list[tuple[str, ETFDataRequirement]] = []

    async def fetch_optimized_data(self, ticker: str, data_requirement: ETFDataRequirement):
        self.calls.append((ticker, data_requirement))
        return None if data_requirement == ETFDataRequirement.NONE else self.etf


class FakeHandler:
    def __init__(self, name: str):
        self.name = name
        self.calls: list[dict[str, Any]] = []

    async def handle(self, **kwargs):
        self.calls.append(kwargs)
        yield {"type": "answer", "body": f"{self.name} answer"}
        yield {"type": "model_used", "body": "test-model"}


def _analyzer(router=None, extractor=None, optimizer=None):
    handlers = {name: FakeHandler(name) for name in ("general", "overview", "detailed", "comparison")}
    analyzer = ETFAnalyzerV2(
        router=router or FakeETFRouter(_routing()),
        ticker_extractor=extractor or FakeTickerExtractor(),
        data_optimizer=optimizer or FakeDataOptimizer(),
        general_handler=handlers["general"],
        overview_handler=handlers["overview"],
        detailed_handler=handlers["detailed"],
        comparison_handler=handlers["comparison"],
    )
    return analyzer, handlers


async def _events(analyzer, **kwargs) -> list[dict]:
    params: dict[str, Any] = {"ticker": "SXR8", "question": "What is the TER?"}
    params.update(kwargs)
    return [e async for e in analyzer.analyze_question(**params)]


def _called(handlers) -> list[str]:
    return [name for name, h in handlers.items() if h.calls]


@pytest.mark.asyncio
async def test_router_result_drives_dispatch_with_db_context():
    router = FakeETFRouter(_routing())
    optimizer = FakeDataOptimizer()
    analyzer, handlers = _analyzer(router=router, optimizer=optimizer)
    history = [{"role": "user", "content": "Who issues it?"}, {"role": "assistant", "content": "iShares"}]

    events = await _events(analyzer, ticker="sxr8", conversation_messages=history, debug_prompt_context=True)

    assert router.calls == [
        {
            "question": "What is the TER?",
            "ticker": "SXR8",
            "conversation_messages": history,
            "etf_name": "iShares Core S&P 500 UCITS ETF",
            "fund_provider": "iShares",
            "index_tracked": "S&P 500",
        }
    ]
    assert optimizer.calls == [("SXR8", ETFDataRequirement.DETAILED)]
    assert _called(handlers) == ["overview"]
    (call,) = handlers["overview"].calls
    assert call["routing"] == _routing()
    assert call["etf_data"] == make_etf()
    assert call["ticker"] == "SXR8"
    assert call["conversation_messages"] == history
    assert call["debug_prompt_context"] is True
    assert events[0] == UNDERSTANDING
    assert [e["type"] for e in events[1:]] == ["answer", "model_used"]


@pytest.mark.asyncio
async def test_detailed_routing_dispatches_to_detailed_handler():
    routing = _routing(ETFQuestionType.ETF_DETAILED_ANALYSIS, ETFDataRequirement.DETAILED, search=False)
    analyzer, handlers = _analyzer(router=FakeETFRouter(routing))
    events = await _events(analyzer, question="Top holdings?")
    assert _called(handlers) == ["detailed"]
    assert handlers["detailed"].calls[0]["etf_data"] == make_etf()
    assert events == [
        UNDERSTANDING,
        {"type": "answer", "body": "detailed answer"},
        {"type": "model_used", "body": "test-model"},
    ]


@pytest.mark.asyncio
async def test_general_routing_gets_no_fund_data():
    routing = _routing(ETFQuestionType.GENERAL_ETF, ETFDataRequirement.NONE)
    analyzer, handlers = _analyzer(router=FakeETFRouter(routing))
    await _events(analyzer, question="What is an ETF?")
    assert _called(handlers) == ["general"]
    assert handlers["general"].calls[0]["etf_data"] is None


@pytest.mark.asyncio
async def test_static_fact_fast_path_turns_search_off():
    analyzer, handlers = _analyzer(router=FakeETFRouter(_routing(search=True)))
    await _events(analyzer, question="When was SXR8 founded?")
    routing = handlers["overview"].calls[0]["routing"]
    assert routing.search_decision.use_google_search is False
    assert routing.search_decision.reason_code == "stable_concept"


@pytest.mark.asyncio
async def test_router_failure_uses_default_overview_routing_with_search_on():
    analyzer, handlers = _analyzer(router=FakeETFRouter(None))
    events = await _events(analyzer, question="When was SXR8 founded?")

    assert _called(handlers) == ["overview"]
    routing = handlers["overview"].calls[0]["routing"]
    assert routing.question_type == ETFQuestionType.ETF_OVERVIEW
    assert routing.data_requirement == ETFDataRequirement.BASIC
    assert routing.search_decision.use_google_search is True  # no fast path on the default routing
    assert routing.search_queries == []
    assert handlers["overview"].calls[0]["etf_data"] == make_etf()
    assert events[0] == UNDERSTANDING


@pytest.mark.asyncio
@pytest.mark.parametrize("ticker", ["", "undefined", "none"])
async def test_router_failure_without_ticker_is_general_and_skips_data_fetch(ticker):
    optimizer = FakeDataOptimizer()
    analyzer, handlers = _analyzer(router=FakeETFRouter(None), optimizer=optimizer)
    events = await _events(analyzer, ticker=ticker, question="What is an ETF?")

    assert optimizer.calls == []
    assert _called(handlers) == ["general"]
    call = handlers["general"].calls[0]
    assert call["routing"].question_type == ETFQuestionType.GENERAL_ETF
    assert call["routing"].data_requirement == ETFDataRequirement.NONE
    assert call["etf_data"] is None
    assert events[0] == UNDERSTANDING


@pytest.mark.asyncio
async def test_two_or_more_tickers_go_to_comparison():
    extractor = FakeTickerExtractor(["SXR8", "VUSA"])
    analyzer, handlers = _analyzer(extractor=extractor)
    await _events(analyzer, question="Compare this with VUSA")

    assert extractor.calls == [("Compare this with VUSA", "SXR8")]
    assert _called(handlers) == ["comparison"]
    call = handlers["comparison"].calls[0]
    assert call["tickers"] == ["SXR8", "VUSA"]
    assert call["short_analysis"] is True
    assert call["routing"] == _routing()


@pytest.mark.asyncio
async def test_comparison_with_router_failure_searches():
    analyzer, handlers = _analyzer(router=FakeETFRouter(None), extractor=FakeTickerExtractor(["SXR8", "VUSA"]))
    await _events(analyzer, question="SXR8 vs VUSA")
    assert handlers["comparison"].calls[0]["routing"].search_decision.use_google_search is True


@pytest.mark.asyncio
async def test_single_extracted_ticker_is_not_a_comparison():
    analyzer, handlers = _analyzer(extractor=FakeTickerExtractor(["SXR8"]))
    await _events(analyzer)
    assert _called(handlers) == ["overview"]


@pytest.mark.asyncio
async def test_extractor_error_continues_without_comparison():
    class _Broken(FakeTickerExtractor):
        async def extract_tickers(self, question, current_ticker=None):
            raise RuntimeError("llm down")

    analyzer, handlers = _analyzer(extractor=_Broken())
    await _events(analyzer)
    assert _called(handlers) == ["overview"]


@pytest.mark.asyncio
async def test_router_starts_before_the_extractor_runs():
    router = FakeETFRouter(_routing())
    seen: list[bool] = []

    class _Extractor(FakeTickerExtractor):
        async def extract_tickers(self, question, current_ticker=None):
            seen.append(router.started)
            return []

    analyzer, _ = _analyzer(router=router, extractor=_Extractor())
    await _events(analyzer)
    assert seen == [True]


@pytest.mark.asyncio
async def test_missing_db_row_still_routes_and_dispatches():
    router = FakeETFRouter(_routing())
    optimizer = FakeDataOptimizer()
    optimizer.etf = None
    analyzer, handlers = _analyzer(router=router, optimizer=optimizer)
    await _events(analyzer)
    assert router.calls[0]["etf_name"] is None
    assert handlers["overview"].calls[0]["etf_data"] is None


@pytest.mark.asyncio
async def test_duplicate_extracted_tickers_are_not_a_comparison():
    analyzer, handlers = _analyzer(extractor=FakeTickerExtractor(["SXR8", "SXR8"]))
    await _events(analyzer, question="Compare it with SXR8")
    assert _called(handlers) == ["overview"]


@pytest.mark.asyncio
async def test_extracted_tickers_are_deduplicated_in_order_before_comparison():
    analyzer, handlers = _analyzer(extractor=FakeTickerExtractor(["SXR8", "CSPX", "sxr8"]))
    await _events(analyzer, question="Is it cheaper than CSPX, and does it hold more Apple?")
    assert _called(handlers) == ["comparison"]
    assert handlers["comparison"].calls[0]["tickers"] == ["SXR8", "CSPX"]


@pytest.mark.asyncio
async def test_hung_extractor_times_out_and_continues_without_comparison():
    class _Hung(FakeTickerExtractor):
        async def extract_tickers(self, question, current_ticker=None):
            await asyncio.sleep(10)
            return ["SXR8", "VUSA"]

    handlers = {name: FakeHandler(name) for name in ("general", "overview", "detailed", "comparison")}
    analyzer = ETFAnalyzerV2(
        router=FakeETFRouter(_routing()),
        ticker_extractor=_Hung(),
        data_optimizer=FakeDataOptimizer(),
        general_handler=handlers["general"],
        overview_handler=handlers["overview"],
        detailed_handler=handlers["detailed"],
        comparison_handler=handlers["comparison"],
        extractor_timeout_seconds=0.05,
    )

    start = asyncio.get_running_loop().time()
    await _events(analyzer, question="SXR8 vs VUSA")
    assert asyncio.get_running_loop().time() - start < 1.0
    assert _called(handlers) == ["overview"]
    assert handlers["overview"].calls[0]["routing"] == _routing()


def test_extractor_timeout_defaults_to_five_seconds():
    analyzer, _ = _analyzer()
    assert analyzer.extractor_timeout_seconds == 5.0


@pytest.mark.asyncio
async def test_understanding_status_goes_out_before_the_db_read_and_router():
    router = FakeETFRouter(_routing())
    optimizer = FakeDataOptimizer()
    analyzer, _ = _analyzer(router=router, optimizer=optimizer)
    stream = analyzer.analyze_question("SXR8", "What is the TER?")
    assert await stream.__anext__() == UNDERSTANDING
    assert optimizer.calls == []
    assert router.calls == []
    assert [e["type"] async for e in stream] == ["answer", "model_used"]  # drain


@pytest.mark.asyncio
async def test_handler_statuses_are_renumbered_onto_the_request_sequence():
    class _StatusHandler(FakeHandler):
        async def handle(self, **kwargs):
            self.calls.append(kwargs)
            yield {"type": "thinking_status", "body": 'Searching for "q"...', "phase": "search", "step": 1}
            yield {"type": "thinking_status", "body": "Writing your answer...", "phase": "analyze", "step": 2}
            yield {"type": "answer", "body": "ok"}

    analyzer = ETFAnalyzerV2(
        router=FakeETFRouter(_routing()),
        ticker_extractor=FakeTickerExtractor(),
        data_optimizer=FakeDataOptimizer(),
        general_handler=FakeHandler("general"),
        overview_handler=_StatusHandler("overview"),
        detailed_handler=FakeHandler("detailed"),
        comparison_handler=FakeHandler("comparison"),
    )
    events = await _events(analyzer)
    assert [(e["body"], e["phase"], e["step"]) for e in events if e["type"] == "thinking_status"] == [
        ("Understanding your question...", "classify", 1),
        ('Searching for "q"...', "search", 2),
        ("Writing your answer...", "analyze", 3),
    ]
