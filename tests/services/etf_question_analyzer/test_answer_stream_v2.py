"""ETF v2 answers pull the blocking LLM stream in a worker thread: a slow answer never blocks the event loop,
and a client disconnect closes the stream.

Fakes only: no LLM, DB or Brave calls.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ai_models.model_name import ModelName
from services.etf_question_analyzer.comparison_handler_v2 import ETFComparisonHandlerV2
from services.etf_question_analyzer.handlers_v2 import ETFOverviewHandlerV2
from services.etf_question_analyzer.types import ETFDataRequirement, ETFQuestionType
from services.question_analyzer.router import ETFRoutingResult
from services.search_decision_engine import SearchDecision
from tests.services.answer_stream_fakes import GatedStream, open_gate_when_waiting
from tests.services.etf_question_analyzer.etf_fakes import (
    FakeAgent,
    FakeAgentFactory,
    FakeRetriever,
    make_etf,
    no_related_questions,
)


class _StreamAgent(FakeAgent):
    """FakeAgent that returns a given stream object, so the test can inspect how it was consumed."""

    def __init__(self, stream: Any):
        super().__init__()
        self.stream = stream

    def generate_content(self, prompt: str, use_google_search: bool = False, **_kwargs):
        super().generate_content(prompt, use_google_search)
        return self.stream


class _RecordingStream:
    def __init__(self, items: list[Any]):
        self.items = items
        self.pulled = 0
        self.closed = False

    def __iter__(self):
        try:
            for item in self.items:
                self.pulled += 1
                yield item
        finally:
            self.closed = True


class _ETFConnector:
    def get_by_ticker(self, ticker: str):
        return make_etf(ticker)


def _routing() -> ETFRoutingResult:
    return ETFRoutingResult(
        question_type=ETFQuestionType.ETF_OVERVIEW,
        data_requirement=ETFDataRequirement.BASIC,
        search_decision=SearchDecision(
            use_google_search=False,
            reason_code="db_data_sufficient",
            confidence=0.9,
            decision_model="router:test",
            decision_fallback="none",
        ),
        search_queries=[],
        standalone_question="What is the TER?",
    )


def _overview(agent: FakeAgent):
    handler = ETFOverviewHandlerV2(
        retriever=FakeRetriever(),
        brave_client=object(),
        agent_factory=FakeAgentFactory(agent),
        related_questions=no_related_questions,
    )
    params: dict[str, Any] = {"ticker": "SXR8", "question": "What is the TER?", "etf_data": make_etf()}
    return handler, params


def _comparison(agent: FakeAgent):
    handler = ETFComparisonHandlerV2(
        connector=_ETFConnector(),
        retriever=FakeRetriever(),
        brave_client=object(),
        agent_factory=FakeAgentFactory(agent),
        related_questions=no_related_questions,
    )
    params: dict[str, Any] = {"tickers": ["SXR8", "VUSA"], "question": "SXR8 vs VUSA?", "short_analysis": True}
    return handler, params


@pytest.mark.asyncio
@pytest.mark.parametrize("make_handler", [_overview, _comparison], ids=["overview", "comparison"])
async def test_etf_answer_stream_does_not_block_event_loop(make_handler):
    stream = GatedStream(["Alpha ", {"type": "url_citation"}, "Beta"])
    handler, params = make_handler(_StreamAgent(stream))

    gate = asyncio.create_task(open_gate_when_waiting(stream))
    events = [
        e
        async for e in handler.handle(routing=_routing(), preferred_model=ModelName.Auto, request_id="req-1", **params)
    ]
    await gate

    assert stream.opened_in_time is True
    assert [e["body"] for e in events if e["type"] == "answer"] == ["Alpha ", "Beta"]


@pytest.mark.asyncio
@pytest.mark.parametrize("make_handler", [_overview, _comparison], ids=["overview", "comparison"])
async def test_etf_close_after_first_answer_closes_llm_stream(make_handler):
    """A client disconnect closes the handler generator; the LLM stream is closed then, not at GC."""
    stream = _RecordingStream(["Alpha ", "Beta ", "Gamma"])
    handler, params = make_handler(_StreamAgent(stream))

    events = handler.handle(routing=_routing(), preferred_model=ModelName.Auto, request_id="req-1", **params)
    async for event in events:
        if event["type"] == "answer":
            break
    await events.aclose()

    assert stream.pulled == 1
    assert stream.closed is True
