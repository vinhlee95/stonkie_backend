"""Tests for DeepAnalysisService orchestration (Phase 3)."""

from __future__ import annotations

import ast
import inspect
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from connectors.agent_connector import AgentEvent, AgentEventType
from services.analysis_progress import AnalysisPhase
from services.analyze_retrieval.schemas import AnalyzeSource


def _make_source(url: str, title: str = "Test") -> AnalyzeSource:
    return AnalyzeSource(
        id=url,
        url=url,
        title=title,
        publisher="Test Publisher",
        published_at=datetime(2025, 1, 1),
        is_trusted=True,
    )


def _mock_connector(events: list[AgentEvent]) -> AsyncMock:
    """Create a mock AgentConnector that yields the given events."""
    connector = AsyncMock()

    async def _stream(*args, **kwargs):
        for event in events:
            yield event

    connector.run_stream = _stream
    return connector


@pytest.fixture
def company_connector():
    return MagicMock()


@pytest.fixture
def company_financial_connector():
    return MagicMock()


@pytest.fixture
def brave_client():
    return MagicMock()


@pytest.mark.asyncio
async def test_analyze_yields_initial_thinking_status(company_connector, company_financial_connector, brave_client):
    """First event is thinking_status with CLASSIFY phase."""
    from services.deep_analysis.service import DeepAnalysisService

    agent_connector = _mock_connector([AgentEvent(type=AgentEventType.RUN_COMPLETE)])

    svc = DeepAnalysisService(
        agent_connector=agent_connector,
        company_connector=company_connector,
        company_financial_connector=company_financial_connector,
        brave_client=brave_client,
    )

    events = []
    async for ev in svc.analyze(
        ticker="AAPL",
        question="What's Apple's revenue?",
        company_name="Apple Inc.",
    ):
        events.append(ev)

    assert events[0]["type"] == "thinking_status"
    assert events[0]["phase"] == AnalysisPhase.CLASSIFY


@pytest.mark.asyncio
async def test_analyze_maps_tool_use_to_thinking_status(company_connector, company_financial_connector, brave_client):
    """TOOL_USE_START(brave_search) maps to thinking_status with SEARCH phase."""
    from services.deep_analysis.service import DeepAnalysisService

    agent_connector = _mock_connector(
        [
            AgentEvent(
                type=AgentEventType.TOOL_USE_START,
                tool_name="brave_search",
                tool_input={"query": "AAPL revenue"},
            ),
            AgentEvent(
                type=AgentEventType.TOOL_RESULT,
                tool_name="brave_search",
                tool_output={"sources": [], "passages": []},
            ),
            AgentEvent(type=AgentEventType.RUN_COMPLETE),
        ]
    )

    svc = DeepAnalysisService(
        agent_connector=agent_connector,
        company_connector=company_connector,
        company_financial_connector=company_financial_connector,
        brave_client=brave_client,
    )

    events = []
    async for ev in svc.analyze(
        ticker="AAPL",
        question="What's Apple's revenue?",
        company_name="Apple Inc.",
    ):
        events.append(ev)

    thinking_events = [e for e in events if e["type"] == "thinking_status"]
    search_events = [e for e in thinking_events if e.get("phase") == AnalysisPhase.SEARCH]
    assert len(search_events) >= 1


@pytest.mark.asyncio
async def test_analyze_maps_financial_tool_to_data_fetch_phase(
    company_connector, company_financial_connector, brave_client
):
    """TOOL_USE_START(get_financial_data) maps to thinking_status with DATA_FETCH phase."""
    from services.deep_analysis.service import DeepAnalysisService

    agent_connector = _mock_connector(
        [
            AgentEvent(
                type=AgentEventType.TOOL_USE_START,
                tool_name="get_financial_data",
                tool_input={"ticker": "AAPL"},
            ),
            AgentEvent(
                type=AgentEventType.TOOL_RESULT,
                tool_name="get_financial_data",
                tool_output=[],
            ),
            AgentEvent(type=AgentEventType.RUN_COMPLETE),
        ]
    )

    svc = DeepAnalysisService(
        agent_connector=agent_connector,
        company_connector=company_connector,
        company_financial_connector=company_financial_connector,
        brave_client=brave_client,
    )

    events = []
    async for ev in svc.analyze(
        ticker="AAPL",
        question="What's Apple's revenue?",
        company_name="Apple Inc.",
    ):
        events.append(ev)

    thinking_events = [e for e in events if e["type"] == "thinking_status"]
    data_fetch_events = [e for e in thinking_events if e.get("phase") == AnalysisPhase.DATA_FETCH]
    assert len(data_fetch_events) >= 1


@pytest.mark.asyncio
async def test_analyze_maps_text_delta_to_answer(company_connector, company_financial_connector, brave_client):
    """TEXT_DELTA events become {type: answer, body: text}."""
    from services.deep_analysis.service import DeepAnalysisService

    agent_connector = _mock_connector(
        [
            AgentEvent(type=AgentEventType.TEXT_DELTA, text="Hello "),
            AgentEvent(type=AgentEventType.TEXT_DELTA, text="world"),
            AgentEvent(type=AgentEventType.RUN_COMPLETE),
        ]
    )

    svc = DeepAnalysisService(
        agent_connector=agent_connector,
        company_connector=company_connector,
        company_financial_connector=company_financial_connector,
        brave_client=brave_client,
    )

    events = []
    async for ev in svc.analyze(
        ticker="AAPL",
        question="What's Apple's revenue?",
        company_name="Apple Inc.",
    ):
        events.append(ev)

    answer_events = [e for e in events if e["type"] == "answer"]
    assert len(answer_events) == 2
    assert answer_events[0]["body"] == "Hello "
    assert answer_events[1]["body"] == "world"


@pytest.mark.asyncio
async def test_analyze_accumulates_sources_and_deduplicates(
    company_connector, company_financial_connector, brave_client
):
    """Multiple brave_search calls — sources are accumulated and deduplicated by URL."""
    from services.deep_analysis.service import DeepAnalysisService

    source_a = _make_source("https://a.com", "Article A")
    source_b = _make_source("https://b.com", "Article B")
    source_a_dup = _make_source("https://a.com", "Article A duplicate")

    agent_connector = _mock_connector(
        [
            AgentEvent(
                type=AgentEventType.TOOL_USE_START,
                tool_name="brave_search",
                tool_input={"query": "q1"},
            ),
            AgentEvent(
                type=AgentEventType.TOOL_RESULT,
                tool_name="brave_search",
                tool_output={
                    "sources": [],
                    "passages": [],
                    "analyze_sources": [source_a, source_b],
                },
            ),
            AgentEvent(
                type=AgentEventType.TOOL_USE_START,
                tool_name="brave_search",
                tool_input={"query": "q2"},
            ),
            AgentEvent(
                type=AgentEventType.TOOL_RESULT,
                tool_name="brave_search",
                tool_output={
                    "sources": [],
                    "passages": [],
                    "analyze_sources": [source_a_dup],
                },
            ),
            AgentEvent(type=AgentEventType.TEXT_DELTA, text="Answer"),
            AgentEvent(type=AgentEventType.RUN_COMPLETE),
        ]
    )

    svc = DeepAnalysisService(
        agent_connector=agent_connector,
        company_connector=company_connector,
        company_financial_connector=company_financial_connector,
        brave_client=brave_client,
    )

    events = []
    async for ev in svc.analyze(
        ticker="AAPL",
        question="What's Apple's revenue?",
        company_name="Apple Inc.",
    ):
        events.append(ev)

    sources_events = [e for e in events if e["type"] == "sources"]
    assert len(sources_events) == 1
    source_urls = [s["url"] for s in sources_events[0]["body"]]
    assert "https://a.com" in source_urls
    assert "https://b.com" in source_urls
    assert len(source_urls) == 2  # deduplicated


@pytest.mark.asyncio
async def test_analyze_enforces_budget_cap(company_connector, company_financial_connector, brave_client):
    """Agent run stops yielding answer after 10 tool calls (budget cap)."""
    from services.deep_analysis.service import DeepAnalysisService

    # Generate 12 tool calls — only 10 should be processed
    events_list = []
    for i in range(12):
        events_list.append(
            AgentEvent(
                type=AgentEventType.TOOL_USE_START,
                tool_name="brave_search",
                tool_input={"query": f"q{i}"},
            )
        )
        events_list.append(
            AgentEvent(
                type=AgentEventType.TOOL_RESULT,
                tool_name="brave_search",
                tool_output={"sources": [], "passages": [], "analyze_sources": []},
            )
        )
    events_list.append(AgentEvent(type=AgentEventType.TEXT_DELTA, text="final"))
    events_list.append(AgentEvent(type=AgentEventType.RUN_COMPLETE))

    agent_connector = _mock_connector(events_list)

    svc = DeepAnalysisService(
        agent_connector=agent_connector,
        company_connector=company_connector,
        company_financial_connector=company_financial_connector,
        brave_client=brave_client,
    )

    events = []
    async for ev in svc.analyze(
        ticker="AAPL",
        question="What's Apple's revenue?",
        company_name="Apple Inc.",
    ):
        events.append(ev)

    tool_thinking = [
        e
        for e in events
        if e["type"] == "thinking_status" and e.get("phase") in (AnalysisPhase.SEARCH, AnalysisPhase.DATA_FETCH)
    ]
    # Should have at most 10 tool-related thinking_status events
    assert len(tool_thinking) <= 10


@pytest.mark.asyncio
async def test_analyze_emits_model_used(company_connector, company_financial_connector, brave_client):
    """model_used event emitted after agent completes."""
    from services.deep_analysis.service import DeepAnalysisService

    agent_connector = _mock_connector([AgentEvent(type=AgentEventType.RUN_COMPLETE)])

    svc = DeepAnalysisService(
        agent_connector=agent_connector,
        company_connector=company_connector,
        company_financial_connector=company_financial_connector,
        brave_client=brave_client,
    )

    events = []
    async for ev in svc.analyze(
        ticker="AAPL",
        question="What's Apple's revenue?",
        company_name="Apple Inc.",
    ):
        events.append(ev)

    model_events = [e for e in events if e["type"] == "model_used"]
    assert len(model_events) == 1
    assert "claude" in model_events[0]["body"].lower() or "sonnet" in model_events[0]["body"].lower()


@pytest.mark.asyncio
async def test_analyze_passes_conversation_history(company_connector, company_financial_connector, brave_client):
    """Conversation messages + current question are passed to the connector."""
    from services.deep_analysis.service import DeepAnalysisService

    captured_messages = []

    async def _stream(system_prompt, messages, tools, max_turns=10):
        captured_messages.extend(messages)
        yield AgentEvent(type=AgentEventType.RUN_COMPLETE)

    agent_connector = AsyncMock()
    agent_connector.run_stream = _stream

    svc = DeepAnalysisService(
        agent_connector=agent_connector,
        company_connector=company_connector,
        company_financial_connector=company_financial_connector,
        brave_client=brave_client,
    )

    conversation = [
        {"role": "user", "content": "Tell me about AAPL"},
        {"role": "assistant", "content": "Apple is a tech company..."},
    ]

    events = []
    async for ev in svc.analyze(
        ticker="AAPL",
        question="What about its revenue?",
        company_name="Apple Inc.",
        conversation_messages=conversation,
    ):
        events.append(ev)

    # Should include conversation history + current question
    assert len(captured_messages) >= 3
    assert captured_messages[-1]["role"] == "user"
    assert "revenue" in captured_messages[-1]["content"]


@pytest.mark.asyncio
async def test_analyze_with_url_context(company_connector, company_financial_connector, brave_client):
    """When extracted_url is provided, has_url=True in prompt building."""
    from services.deep_analysis.service import DeepAnalysisService

    captured_prompt = []

    async def _stream(system_prompt, messages, tools, max_turns=10):
        captured_prompt.append(system_prompt)
        yield AgentEvent(type=AgentEventType.RUN_COMPLETE)

    agent_connector = AsyncMock()
    agent_connector.run_stream = _stream

    svc = DeepAnalysisService(
        agent_connector=agent_connector,
        company_connector=company_connector,
        company_financial_connector=company_financial_connector,
        brave_client=brave_client,
    )

    events = []
    async for ev in svc.analyze(
        ticker="AAPL",
        question="Analyze this filing",
        company_name="Apple Inc.",
        extracted_url="https://sec.gov/some-filing.pdf",
    ):
        events.append(ev)

    assert "URL" in captured_prompt[0] or "url" in captured_prompt[0].lower()
    assert "read_url" in captured_prompt[0]


@pytest.mark.asyncio
async def test_analyze_depends_on_interface_not_implementation():
    """service.py imports AgentConnector interface, NOT ClaudeAgentConnector."""
    import services.deep_analysis.service as svc_module

    source_file = inspect.getfile(svc_module)
    with open(source_file) as f:
        tree = ast.parse(f.read())

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")

    # Should NOT import the concrete implementation
    for imp in imports:
        assert "claude_agent_connector" not in imp, f"service.py must not import claude_agent_connector, found: {imp}"
