from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ai_models.model_name import ModelName
from services.analyze_retrieval.schemas import AnalyzeRetrievalResult, AnalyzeSource
from services.search_decision_engine import SearchDecision


def _event_types(events: list[dict]) -> list[str]:
    return [event["type"] for event in events]


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.MultiAgent")
@patch("services.question_analyzer.handlers_v2.CompanyConnector")
async def test_no_search_emits_v1_like_sequence(mock_company_connector_cls, mock_multi_agent_cls):
    from services.question_analyzer.handlers_v2 import CompanyGeneralHandlerV2

    mock_company_connector = MagicMock()
    mock_company_connector.get_by_ticker.return_value = SimpleNamespace(name="Apple Inc.", country="United States")
    mock_company_connector_cls.return_value = mock_company_connector

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["Alpha ", "Beta"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanyGeneralHandlerV2(company_connector=mock_company_connector)

    async def _fake_related_questions(*_args, **_kwargs):
        yield {"type": "related_question", "body": "RQ1"}
        yield {"type": "related_question", "body": "RQ2"}

    handler._generate_related_questions = _fake_related_questions  # type: ignore[attr-defined]

    decision = SearchDecision(
        use_google_search=False,
        reason_code="stable_concept",
        confidence=0.99,
        decision_model="test",
        decision_fallback="none",
    )

    events: list[dict] = []
    async for event in handler.handle(
        ticker="AAPL",
        question="What does Apple do?",
        search_decision=decision,
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
    ):
        events.append(event)

    assert events[0]["type"] == "thinking_status"
    assert _event_types(events).count("answer") == 2
    assert any(event["type"] == "model_used" for event in events)
    assert _event_types(events)[-2:] == ["related_question", "related_question"]


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.MultiAgent")
@patch("services.question_analyzer.handlers_v2.CompanyConnector")
async def test_no_search_never_emits_sources_event(mock_company_connector_cls, mock_multi_agent_cls):
    from services.question_analyzer.handlers_v2 import CompanyGeneralHandlerV2

    mock_company_connector = MagicMock()
    mock_company_connector.get_by_ticker.return_value = SimpleNamespace(name="Apple Inc.", country="United States")
    mock_company_connector_cls.return_value = mock_company_connector

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["Answer only"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanyGeneralHandlerV2(company_connector=mock_company_connector)

    async def _fake_related_questions(*_args, **_kwargs):
        if False:
            yield {"type": "related_question", "body": "unused"}

    handler._generate_related_questions = _fake_related_questions  # type: ignore[attr-defined]

    decision = SearchDecision(
        use_google_search=False,
        reason_code="stable_concept",
        confidence=0.99,
        decision_model="test",
        decision_fallback="none",
    )

    events: list[dict] = []
    async for event in handler.handle(
        ticker="AAPL",
        question="What does Apple do?",
        search_decision=decision,
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
    ):
        events.append(event)

    assert all(event["type"] != "sources" for event in events)


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.retrieve_for_analyze")
@patch("services.question_analyzer.handlers_v2.MultiAgent")
@patch("services.question_analyzer.handlers_v2.CompanyConnector")
async def test_search_on_emits_trusted_publishers_and_final_sources_once(
    mock_company_connector_cls, mock_multi_agent_cls, mock_retrieve_for_analyze
):
    from services.question_analyzer.handlers_v2 import CompanyGeneralHandlerV2

    mock_company_connector = MagicMock()
    mock_company_connector.get_by_ticker.return_value = SimpleNamespace(name="Apple Inc.", country="United States")
    mock_company_connector_cls.return_value = mock_company_connector

    retrieved_sources = [
        AnalyzeSource(
            id="s_1",
            url="https://www.reuters.com/world/us/example",
            title="Reuters title",
            publisher="Reuters",
            published_at=None,
            is_trusted=True,
        ),
        AnalyzeSource(
            id="s_2",
            url="https://www.example-blog.com/post",
            title="Blog title",
            publisher="Example Blog",
            published_at=None,
            is_trusted=False,
        ),
    ]
    mock_retrieve_for_analyze.return_value = AnalyzeRetrievalResult(
        sources=retrieved_sources,
        query="What changed?",
        market="GLOBAL",
        request_id="req-1",
    )

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["Alpha [1]", " and beta [2]"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanyGeneralHandlerV2(company_connector=mock_company_connector)

    async def _fake_related_questions(*_args, **_kwargs):
        if False:
            yield {"type": "related_question", "body": "unused"}

    handler._generate_related_questions = _fake_related_questions  # type: ignore[attr-defined]

    decision = SearchDecision(
        use_google_search=True,
        reason_code="latest_info",
        confidence=0.9,
        decision_model="test",
        decision_fallback="none",
    )

    events: list[dict] = []
    async for event in handler.handle(
        ticker="AAPL",
        question="What changed?",
        search_decision=decision,
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="req-1",
    ):
        events.append(event)

    thinking_events = [event for event in events if event["type"] == "thinking_status"]
    assert len(thinking_events) == 2
    assert "Reuters" in thinking_events[1]["body"]
    assert "Example Blog" not in thinking_events[1]["body"]
    assert mock_retrieve_for_analyze.call_count == 1

    answer_events = [event for event in events if event["type"] == "answer"]
    assert answer_events[0]["body"] == "Alpha [1]"
    assert answer_events[1]["body"] == " and beta [2]"

    sources_events = [event for event in events if event["type"] == "sources"]
    assert len(sources_events) == 1
    assert [source["source_id"] for source in sources_events[0]["body"]["sources"]] == ["s_1", "s_2"]


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.retrieve_for_analyze")
@patch("services.question_analyzer.handlers_v2.MultiAgent")
@patch("services.question_analyzer.handlers_v2.CompanyConnector")
async def test_search_on_dedupes_trusted_publishers_in_single_status(
    mock_company_connector_cls, mock_multi_agent_cls, mock_retrieve_for_analyze
):
    from services.question_analyzer.handlers_v2 import CompanyGeneralHandlerV2

    mock_company_connector = MagicMock()
    mock_company_connector.get_by_ticker.return_value = SimpleNamespace(name="Apple Inc.", country="United States")
    mock_company_connector_cls.return_value = mock_company_connector

    mock_retrieve_for_analyze.return_value = AnalyzeRetrievalResult(
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
        query="What changed?",
        market="GLOBAL",
        request_id="req-2",
    )

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["A [1]"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanyGeneralHandlerV2(company_connector=mock_company_connector)

    async def _fake_related_questions(*_args, **_kwargs):
        if False:
            yield {"type": "related_question", "body": "unused"}

    handler._generate_related_questions = _fake_related_questions  # type: ignore[attr-defined]

    decision = SearchDecision(
        use_google_search=True,
        reason_code="latest_info",
        confidence=0.9,
        decision_model="test",
        decision_fallback="none",
    )

    events: list[dict] = []
    async for event in handler.handle(
        ticker="AAPL",
        question="What changed?",
        search_decision=decision,
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="req-2",
    ):
        events.append(event)

    publisher_status = [event for event in events if event["type"] == "thinking_status"][1]["body"]
    assert publisher_status.count("Reuters") == 1


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.retrieve_for_analyze")
@patch("services.question_analyzer.handlers_v2.MultiAgent")
@patch("services.question_analyzer.handlers_v2.CompanyConnector")
async def test_search_on_with_no_citations_emits_empty_sources_list(
    mock_company_connector_cls, mock_multi_agent_cls, mock_retrieve_for_analyze
):
    from services.question_analyzer.handlers_v2 import CompanyGeneralHandlerV2

    mock_company_connector = MagicMock()
    mock_company_connector.get_by_ticker.return_value = SimpleNamespace(name="Apple Inc.", country="United States")
    mock_company_connector_cls.return_value = mock_company_connector

    mock_retrieve_for_analyze.return_value = AnalyzeRetrievalResult(
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
        query="What changed?",
        market="GLOBAL",
        request_id="req-3",
    )

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["Answer with no citations."])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanyGeneralHandlerV2(company_connector=mock_company_connector)

    async def _fake_related_questions(*_args, **_kwargs):
        if False:
            yield {"type": "related_question", "body": "unused"}

    handler._generate_related_questions = _fake_related_questions  # type: ignore[attr-defined]

    decision = SearchDecision(
        use_google_search=True,
        reason_code="latest_info",
        confidence=0.9,
        decision_model="test",
        decision_fallback="none",
    )

    events: list[dict] = []
    async for event in handler.handle(
        ticker="AAPL",
        question="What changed?",
        search_decision=decision,
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="req-3",
    ):
        events.append(event)

    sources_events = [event for event in events if event["type"] == "sources"]
    assert len(sources_events) == 1
    assert sources_events[0]["body"]["sources"] == []
