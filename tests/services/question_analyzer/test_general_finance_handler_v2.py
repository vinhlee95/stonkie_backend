from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ai_models.model_name import ModelName
from services.analyze_retrieval.schemas import AnalyzeRetrievalResult, AnalyzeSource
from services.search_decision_engine import SearchDecision


def _event_types(events: list[dict]) -> list[str]:
    return [event["type"] for event in events]


def _decision(use_search: bool) -> SearchDecision:
    return SearchDecision(
        use_google_search=use_search,
        reason_code="stable_concept" if not use_search else "latest_info",
        confidence=0.9,
        decision_model="test",
        decision_fallback="none",
    )


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_no_search_emits_v1_like_sequence(mock_multi_agent_cls):
    from services.question_analyzer.handlers_v2 import GeneralFinanceHandlerV2

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["Concept ", "explained"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = GeneralFinanceHandlerV2()

    async def _fake_related(*_a, **_k):
        yield {"type": "related_question", "body": "RQ1"}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        question="What is P/E ratio?",
        search_decision=_decision(False),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
    ):
        events.append(event)

    assert events[0]["type"] == "thinking_status"
    assert _event_types(events).count("answer") == 2
    assert any(event["type"] == "model_used" for event in events)
    assert events[-1]["type"] == "related_question"
    assert all(event["type"] != "sources" for event in events)


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_no_search_never_emits_sources_event(mock_multi_agent_cls):
    from services.question_analyzer.handlers_v2 import GeneralFinanceHandlerV2

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["Answer only"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = GeneralFinanceHandlerV2()

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        question="What is P/E?",
        search_decision=_decision(False),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
    ):
        events.append(event)

    assert all(event["type"] != "sources" for event in events)


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.retrieve_for_analyze")
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_search_on_emits_trusted_publishers_and_final_sources_once(mock_multi_agent_cls, mock_retrieve):
    from services.question_analyzer.handlers_v2 import GeneralFinanceHandlerV2

    sources = [
        AnalyzeSource(
            id="s_1",
            url="https://www.reuters.com/a",
            title="A",
            publisher="Reuters",
            published_at=None,
            is_trusted=True,
        ),
        AnalyzeSource(
            id="s_2",
            url="https://example-blog.com/b",
            title="B",
            publisher="Example Blog",
            published_at=None,
            is_trusted=False,
        ),
    ]
    mock_retrieve.return_value = AnalyzeRetrievalResult(
        sources=sources,
        query="latest CPI",
        market="GLOBAL",
        request_id="req-1",
    )

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["Latest ", "and"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = GeneralFinanceHandlerV2()

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        question="What is the latest CPI?",
        search_decision=_decision(True),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="req-1",
    ):
        events.append(event)

    thinking_events = [e for e in events if e["type"] == "thinking_status"]
    assert len(thinking_events) == 2
    assert "Reuters" in thinking_events[1]["body"]
    assert "Example Blog" not in thinking_events[1]["body"]

    answer_events = [e for e in events if e["type"] == "answer"]
    assert "".join(event["body"] for event in answer_events) == "Latest and"

    sources_events = [e for e in events if e["type"] == "sources"]
    assert len(sources_events) == 1
    assert [s["source_id"] for s in sources_events[0]["body"]] == ["s_1", "s_2"]


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.retrieve_for_analyze")
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_search_on_dedupes_trusted_publishers_in_single_status(mock_multi_agent_cls, mock_retrieve):
    from services.question_analyzer.handlers_v2 import GeneralFinanceHandlerV2

    mock_retrieve.return_value = AnalyzeRetrievalResult(
        sources=[
            AnalyzeSource(
                id="s_1",
                url="https://www.reuters.com/a",
                title="A",
                publisher="Reuters",
                published_at=None,
                is_trusted=True,
            ),
            AnalyzeSource(
                id="s_2",
                url="https://www.reuters.com/b",
                title="B",
                publisher="Reuters",
                published_at=None,
                is_trusted=True,
            ),
        ],
        query="q",
        market="GLOBAL",
        request_id="req-2",
    )

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["A [1]"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = GeneralFinanceHandlerV2()

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        question="q",
        search_decision=_decision(True),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="req-2",
    ):
        events.append(event)

    publisher_status = [e for e in events if e["type"] == "thinking_status"][1]["body"]
    assert publisher_status.count("Reuters") == 1


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.retrieve_for_analyze")
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_search_on_emits_all_retrieved_sources_regardless_of_inline_citations(
    mock_multi_agent_cls, mock_retrieve
):
    from services.question_analyzer.handlers_v2 import GeneralFinanceHandlerV2

    mock_retrieve.return_value = AnalyzeRetrievalResult(
        sources=[
            AnalyzeSource(
                id="s_1",
                url="https://www.reuters.com/a",
                title="A",
                publisher="Reuters",
                published_at=None,
                is_trusted=True,
            ),
        ],
        query="q",
        market="GLOBAL",
        request_id="req-3",
    )

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["Answer no citations."])
    mock_multi_agent_cls.return_value = mock_agent

    handler = GeneralFinanceHandlerV2()

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        question="q",
        search_decision=_decision(True),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="req-3",
    ):
        events.append(event)

    sources_events = [e for e in events if e["type"] == "sources"]
    assert len(sources_events) == 1
    assert [s["source_id"] for s in sources_events[0]["body"]] == ["s_1"]
