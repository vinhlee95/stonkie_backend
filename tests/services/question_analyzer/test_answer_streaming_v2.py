"""Answer chunks stream lazily: the first `answer` event goes out before the LLM finishes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ai_models.model_name import ModelName
from services.question_analyzer.handlers_v2 import _iter_answer_chunks
from services.search_decision_engine import SearchDecision

_CITATION = {"type": "url_citation", "url": "https://example.com/a", "title": "a"}


class _RecordingStream:
    """Fake MultiAgent.generate_content() result that counts how many chunks were pulled."""

    def __init__(self, items: list):
        self.items = items
        self.pulled = 0

    def __iter__(self):
        for item in self.items:
            self.pulled += 1
            yield item


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


async def _collect_with_pull_count(handler_events, stream: _RecordingStream):
    events: list[dict] = []
    pulled_at_first_answer = None
    async for event in handler_events:
        if event["type"] == "answer" and pulled_at_first_answer is None:
            pulled_at_first_answer = stream.pulled
        events.append(event)
    return events, pulled_at_first_answer


def test_iter_answer_chunks_is_lazy_and_skips_dicts():
    stream = _RecordingStream(["Hello", _CITATION, " world"])
    chunks = _iter_answer_chunks(stream)

    assert stream.pulled == 0
    assert next(chunks) == "Hello"
    assert stream.pulled == 1
    assert list(chunks) == [" world"]
    assert stream.pulled == 3


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_general_finance_handler_streams_first_chunk_before_generation_ends(mock_multi_agent_cls):
    from services.question_analyzer.handlers_v2 import GeneralFinanceHandlerV2

    stream = _RecordingStream(["P/E ", _CITATION, "is price ", "over earnings"])
    mock_multi_agent_cls.return_value = _fake_agent(stream)

    handler = GeneralFinanceHandlerV2()
    handler._generate_related_questions = _no_related  # type: ignore[attr-defined]

    events, pulled_at_first_answer = await _collect_with_pull_count(
        handler.handle(
            question="What is a P/E ratio?",
            search_decision=_decision(False),
            use_url_context=False,
            preferred_model=ModelName.Auto,
            conversation_messages=None,
        ),
        stream,
    )

    assert pulled_at_first_answer == 1
    answers = [e["body"] for e in events if e["type"] == "answer"]
    assert answers == ["P/E ", "is price ", "over earnings"]
    types = [e["type"] for e in events]
    last_answer = max(i for i, t in enumerate(types) if t == "answer")
    assert types.index("model_used") > last_answer


@pytest.mark.asyncio
@patch("services.question_analyzer.comparison_handler_v2.retrieve_for_analyze")
@patch("services.question_analyzer.comparison_handler_v2.MultiAgent")
async def test_comparison_handler_streams_first_chunk_before_generation_ends(mock_multi_agent_cls, mock_retrieve):
    from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2
    from services.question_analyzer.context_builders.comparison_builder import CompanyComparisonData

    stream = _RecordingStream(["AAPL ", _CITATION, "vs MSFT"])
    mock_multi_agent_cls.return_value = _fake_agent(stream)

    handler = CompanyComparisonHandlerV2()

    async def fake_fetch(self_, tickers_arg):
        return [CompanyComparisonData(ticker=t, data_source="database") for t in tickers_arg]

    handler._fetch_companies_parallel = fake_fetch.__get__(handler, type(handler))
    handler._generate_related_questions = _no_related  # type: ignore[attr-defined]

    events, pulled_at_first_answer = await _collect_with_pull_count(
        handler.handle(
            tickers=["AAPL", "MSFT"],
            question="AAPL or MSFT?",
            search_decision=_decision(False),
            short_analysis=True,
            preferred_model=ModelName.Auto,
            conversation_messages=None,
            request_id="rq",
        ),
        stream,
    )

    mock_retrieve.assert_not_called()
    assert pulled_at_first_answer == 1
    answers = [e["body"] for e in events if e["type"] == "answer"]
    assert answers == ["AAPL ", "vs MSFT"]
    types = [e["type"] for e in events]
    last_answer = max(i for i, t in enumerate(types) if t == "answer")
    assert types.index("model_used") > last_answer
