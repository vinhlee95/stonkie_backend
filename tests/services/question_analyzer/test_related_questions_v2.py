"""Related questions run concurrently with the answer in every v2 handler (WP7). Fakes only."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from ai_models.model_name import ModelName
from services.search_decision_engine import SearchDecision

_HANDLERS = "services.question_analyzer.handlers_v2"


def _decision(use_search: bool = False) -> SearchDecision:
    return SearchDecision(
        use_google_search=use_search,
        reason_code="latest_info" if use_search else "stable_concept",
        confidence=0.9,
        decision_model="test",
        decision_fallback="none",
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


def _slow_related(delay: float, questions=("Q1?", "Q2?")):
    async def related(*_a, **_k):
        await asyncio.to_thread(time.sleep, delay)
        for q in questions:
            yield {"type": "related_question", "body": q}

    return related


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.MultiAgent")
async def test_related_questions_generate_during_the_answer_and_follow_model_used(mock_agent_cls):
    from services.question_analyzer.handlers_v2 import GeneralFinanceHandlerV2

    mock_agent_cls.return_value = _agent(chunks=("a", "b", "c"), delay=0.1)  # 0.3s blocking answer stream
    handler = GeneralFinanceHandlerV2()
    handler._generate_related_questions = _slow_related(0.3)  # type: ignore[attr-defined]

    start = time.perf_counter()
    stamps: list[tuple[str, float]] = []
    async for e in handler.handle(question="What is P/E?", search_decision=_decision(False), use_url_context=False):
        stamps.append((e["type"], time.perf_counter() - start))
    elapsed = time.perf_counter() - start

    types = [t for t, _ in stamps]
    assert types[-3:] == ["model_used", "related_question", "related_question"]
    model_used_at = dict(stamps)["model_used"]
    assert stamps[-1][1] - model_used_at < 0.2
    assert elapsed < 0.5, f"related questions ran after the answer ({elapsed:.2f}s)"


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.MultiAgent")
async def test_related_questions_task_is_cancelled_when_the_answer_fails(mock_agent_cls):
    from services.question_analyzer.handlers_v2 import GeneralFinanceHandlerV2

    agent = MagicMock()
    agent.generate_content.side_effect = RuntimeError("llm down")
    mock_agent_cls.return_value = agent
    handler = GeneralFinanceHandlerV2()
    finished = asyncio.Event()
    started = asyncio.Event()

    async def related(*_a, **_k):
        started.set()
        try:
            await asyncio.sleep(5)
            yield {"type": "related_question", "body": "never"}
        finally:
            finished.set()

    handler._generate_related_questions = related  # type: ignore[attr-defined]

    with pytest.raises(RuntimeError):
        async for _ in handler.handle(question="q", search_decision=_decision(False), use_url_context=False):
            pass
    await asyncio.wait_for(finished.wait(), timeout=1)
    assert started.is_set()


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.MultiAgent")
async def test_related_questions_task_is_cancelled_on_client_disconnect(mock_agent_cls):
    from services.question_analyzer.handlers_v2 import GeneralFinanceHandlerV2

    mock_agent_cls.return_value = _agent()
    handler = GeneralFinanceHandlerV2()
    finished = asyncio.Event()

    async def related(*_a, **_k):
        try:
            await asyncio.sleep(5)
            yield {"type": "related_question", "body": "never"}
        finally:
            finished.set()

    handler._generate_related_questions = related  # type: ignore[attr-defined]

    stream = handler.handle(question="q", search_decision=_decision(False), use_url_context=False)
    async for event in stream:
        if event["type"] == "answer":
            break
    await stream.aclose()
    await asyncio.wait_for(finished.wait(), timeout=1)


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.MultiAgent")
async def test_related_questions_failure_does_not_break_the_stream(mock_agent_cls):
    from services.question_analyzer.handlers_v2 import GeneralFinanceHandlerV2

    mock_agent_cls.return_value = _agent()
    handler = GeneralFinanceHandlerV2()

    async def related(*_a, **_k):
        raise RuntimeError("related LLM down")
        yield {}

    handler._generate_related_questions = related  # type: ignore[attr-defined]

    events = [e async for e in handler.handle(question="q", search_decision=_decision(False), use_url_context=False)]

    assert events[-1]["type"] == "model_used"


@pytest.mark.asyncio
@patch(f"{_HANDLERS}.MultiAgent")
async def test_generate_related_questions_uses_flash_lite_and_comparison_prompt(mock_agent_cls):
    from services.question_analyzer.handlers_v2 import generate_related_questions

    agent = MagicMock()
    agent.generate_content_by_lines.return_value = iter(["Follow-up one?", "Follow-up two?"])
    mock_agent_cls.return_value = agent

    events = [e async for e in generate_related_questions("AAPL vs MSFT?", comparison=True, count=2)]

    mock_agent_cls.assert_called_once_with(model_name=ModelName.Gemini31FlashLite)
    kwargs = agent.generate_content_by_lines.call_args.kwargs
    assert kwargs["max_lines"] == 2
    assert 'Based on this comparison question: "AAPL vs MSFT?"' in kwargs["prompt"]
    assert events == [
        {"type": "related_question", "body": "Follow-up one?"},
        {"type": "related_question", "body": "Follow-up two?"},
    ]


@pytest.mark.asyncio
@patch("services.question_analyzer.comparison_handler_v2.MultiAgent")
async def test_comparison_related_questions_overlap_answer_and_follow_data_sources(mock_agent_cls):
    from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2
    from services.question_analyzer.context_builders.comparison_builder import CompanyComparisonData

    mock_agent_cls.return_value = _agent(chunks=("a", "b", "c"), delay=0.1)
    handler = CompanyComparisonHandlerV2(company_connector=MagicMock(), financial_connector=MagicMock())

    async def fetch(tickers):
        return [CompanyComparisonData(ticker=t, data_source="training_data") for t in tickers]

    handler._fetch_companies_parallel = fetch  # type: ignore[method-assign]
    handler._generate_related_questions = _slow_related(0.3)  # type: ignore[attr-defined]

    start = time.perf_counter()
    events = [
        e
        async for e in handler.handle(
            tickers=["AAPL", "MSFT"],
            question="AAPL vs MSFT?",
            search_decision=_decision(False),
            short_analysis=True,
            preferred_model=ModelName.Auto,
        )
    ]
    elapsed = time.perf_counter() - start

    assert [e["type"] for e in events][-4:] == ["model_used", "data_sources", "related_question", "related_question"]
    assert elapsed < 0.5, f"related questions ran after the answer ({elapsed:.2f}s)"
