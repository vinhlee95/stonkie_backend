"""Answer chunks stream lazily: the first `answer` event goes out before the LLM finishes.

The blocking LLM stream is pulled in a worker thread, so a slow answer never blocks the event loop, and a
client disconnect closes it.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_models.model_name import ModelName
from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2
from services.question_analyzer.handlers_v2 import (
    CompanyGeneralHandlerV2,
    CompanySpecificFinanceHandlerV2,
    GeneralFinanceHandlerV2,
    _iter_answer_chunks,
)
from services.question_analyzer.types import FinancialDataRequirement, QuestionType
from services.search_decision_engine import SearchDecision
from services.shared.answer_stream import answer_stream
from tests.services.answer_stream_fakes import GatedStream, open_gate_when_waiting
from tests.services.routing_fakes import make_routing

_CITATION = {"type": "url_citation", "url": "https://example.com/a", "title": "a"}


class _RecordingStream:
    """Fake MultiAgent.generate_content() result that counts how many chunks were pulled."""

    def __init__(self, items: list):
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


def _decision(use_search: bool) -> SearchDecision:
    return SearchDecision(
        use_google_search=use_search,
        reason_code="latest_info" if use_search else "stable_concept",
        confidence=0.9,
        decision_model="test",
        decision_fallback="none",
    )


def _fake_agent(stream: _RecordingStream) -> MagicMock:
    agent = MagicMock()
    agent.model_name = "test-model"
    agent.generate_content.return_value = stream
    return agent


async def _no_related(*_a, **_k):
    if False:
        yield {}


def _company_connector() -> MagicMock:
    connector = MagicMock()
    connector.get_by_ticker.return_value = SimpleNamespace(name="Apple Inc.", country="United States")
    connector.get_fundamental_data.return_value = SimpleNamespace(
        name="Apple Inc.",
        sector="Technology",
        industry="Consumer Electronics",
        market_cap=1_000_000,
        pe_ratio=25.0,
        basic_eps=6.0,
        revenue=500_000,
        net_income=100_000,
        dividend_yield=0.5,
    )
    return connector


def _general_finance():
    handler = GeneralFinanceHandlerV2()
    kwargs = dict(
        question="What is a P/E ratio?",
        use_url_context=False,
        conversation_messages=None,
        routing=make_routing(QuestionType.GENERAL_FINANCE.value),
    )
    return handler, kwargs


def _company_general():
    handler = CompanyGeneralHandlerV2(company_connector=_company_connector())
    kwargs = dict(
        ticker="AAPL",
        question="What does Apple do?",
        use_url_context=False,
        conversation_messages=None,
        routing=make_routing(QuestionType.COMPANY_GENERAL.value),
    )
    return handler, kwargs


def _company_specific():
    optimizer = MagicMock()
    optimizer.fetch_optimized_data = AsyncMock(return_value=({"Name": "Apple Inc."}, [], []))
    handler = CompanySpecificFinanceHandlerV2(company_connector=_company_connector(), data_optimizer=optimizer)
    kwargs = dict(
        ticker="AAPL",
        question="What's Apple's revenue?",
        use_url_context=False,
        conversation_messages=None,
        routing=make_routing(
            QuestionType.COMPANY_SPECIFIC_FINANCE.value, data_requirement=FinancialDataRequirement.BASIC
        ),
    )
    return handler, kwargs


def _company_specific_fallback():
    """No ticker + conversation: answered from the conversation by _stream_fallback_answer."""
    handler, kwargs = _company_specific()
    kwargs.update(
        ticker="undefined",
        question="And what's next?",
        conversation_messages=[
            {"role": "user", "content": "Tell me about Apple's revenue."},
            {"role": "assistant", "content": "Apple revenue is $400B."},
        ],
    )
    return handler, kwargs


def _comparison():
    financial_connector = MagicMock()
    financial_connector.get_company_quarterly_financial_statements_recent.return_value = []
    handler = CompanyComparisonHandlerV2(
        company_connector=_company_connector(), financial_connector=financial_connector
    )
    kwargs = dict(
        tickers=["AAPL", "MSFT"],
        question="AAPL or MSFT?",
        short_analysis=True,
        conversation_messages=None,
        request_id="rq",
    )
    return handler, kwargs


_HANDLERS = [_general_finance, _company_general, _company_specific, _company_specific_fallback, _comparison]
_HANDLER_IDS = ["general_finance", "company_general", "company_specific", "company_specific_fallback", "comparison"]


def test_iter_answer_chunks_is_lazy_and_skips_dicts():
    stream = _RecordingStream(["Hello", _CITATION, " world"])
    chunks = _iter_answer_chunks(stream)

    assert stream.pulled == 0
    assert next(chunks) == "Hello"
    assert stream.pulled == 1
    assert list(chunks) == [" world"]
    assert stream.pulled == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "make_handler",
    _HANDLERS,
    ids=_HANDLER_IDS,
)
@patch("services.question_analyzer.comparison_handler_v2.retrieve_for_analyze")
@patch("services.question_analyzer.comparison_handler_v2.MultiAgent")
@patch("services.question_analyzer.handlers_v2.retrieve_for_analyze")
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_handler_streams_first_chunk_before_generation_ends(
    mock_agent_cls, mock_retrieve, mock_cmp_agent_cls, mock_cmp_retrieve, make_handler
):
    stream = _RecordingStream(["First ", _CITATION, "second ", "third"])
    mock_agent_cls.return_value = _fake_agent(stream)
    mock_cmp_agent_cls.return_value = _fake_agent(stream)

    handler, kwargs = make_handler()
    handler._generate_related_questions = _no_related  # type: ignore[attr-defined]

    events: list[dict] = []
    pulled_at_first_answer = None
    async for event in handler.handle(search_decision=_decision(False), preferred_model=ModelName.Auto, **kwargs):
        if event["type"] == "answer" and pulled_at_first_answer is None:
            pulled_at_first_answer = stream.pulled
        events.append(event)

    mock_retrieve.assert_not_called()
    mock_cmp_retrieve.assert_not_called()
    assert pulled_at_first_answer == 1
    assert [e["body"] for e in events if e["type"] == "answer"] == ["First ", "second ", "third"]
    types = [e["type"] for e in events]
    last_answer = max(i for i, t in enumerate(types) if t == "answer")
    assert types.index("model_used") > last_answer


@pytest.mark.asyncio
@pytest.mark.parametrize("make_handler", _HANDLERS, ids=_HANDLER_IDS)
@patch("services.question_analyzer.comparison_handler_v2.retrieve_for_analyze")
@patch("services.question_analyzer.comparison_handler_v2.MultiAgent")
@patch("services.question_analyzer.handlers_v2.retrieve_for_analyze")
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_handler_answer_stream_does_not_block_event_loop(
    mock_agent_cls, mock_retrieve, mock_cmp_agent_cls, mock_cmp_retrieve, make_handler
):
    stream = GatedStream(["First ", _CITATION, "second"])
    mock_agent_cls.return_value = _fake_agent(stream)
    mock_cmp_agent_cls.return_value = _fake_agent(stream)
    handler, kwargs = make_handler()
    handler._generate_related_questions = _no_related  # type: ignore[attr-defined]

    gate = asyncio.create_task(open_gate_when_waiting(stream))
    events = [
        e async for e in handler.handle(search_decision=_decision(False), preferred_model=ModelName.Auto, **kwargs)
    ]
    await gate

    assert stream.opened_in_time is True
    assert [e["body"] for e in events if e["type"] == "answer"] == ["First ", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("make_handler", _HANDLERS, ids=_HANDLER_IDS)
@patch("services.question_analyzer.comparison_handler_v2.retrieve_for_analyze")
@patch("services.question_analyzer.comparison_handler_v2.MultiAgent")
@patch("services.question_analyzer.handlers_v2.retrieve_for_analyze")
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_handler_close_after_first_answer_closes_llm_stream(
    mock_agent_cls, mock_retrieve, mock_cmp_agent_cls, mock_cmp_retrieve, make_handler
):
    """A client disconnect closes the handler generator; the LLM stream is closed then, not at GC."""
    stream = _RecordingStream(["First ", "second ", "third"])
    mock_agent_cls.return_value = _fake_agent(stream)
    mock_cmp_agent_cls.return_value = _fake_agent(stream)
    handler, kwargs = make_handler()
    handler._generate_related_questions = _no_related  # type: ignore[attr-defined]

    events = handler.handle(search_decision=_decision(False), preferred_model=ModelName.Auto, **kwargs)
    async for event in events:
        if event["type"] == "answer":
            break
    await events.aclose()

    assert stream.pulled == 1
    assert stream.closed is True


@pytest.mark.asyncio
async def test_answer_stream_cancelled_consumer_stops_pulling_after_item_in_flight():
    in_flight = threading.Event()
    release = threading.Event()
    pulled: list[int] = []

    def stream():
        for i in range(5):
            pulled.append(i)
            if i == 1:
                in_flight.set()
                release.wait(timeout=2)
            yield i

    async def consume():
        async with answer_stream(stream()) as chunks:
            async for _ in chunks:
                pass

    task = asyncio.create_task(consume())
    assert await asyncio.to_thread(in_flight.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await asyncio.sleep(0.1)  # the in-flight next() returns; nothing pulls again

    assert pulled == [0, 1]
