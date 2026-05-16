"""Tests for AgentConnector interface + ClaudeAgentConnector (Phase 0 TDD)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from connectors.agent_connector import AgentConnector, AgentEvent, AgentEventType, AgentTool
from connectors.claude_agent_connector import ClaudeAgentConnector

# ── Interface tests ──────────────────────────────────────────────


class TestAgentEventType:
    def test_has_four_members(self):
        assert set(AgentEventType) == {
            AgentEventType.TOOL_USE_START,
            AgentEventType.TOOL_RESULT,
            AgentEventType.TEXT_DELTA,
            AgentEventType.RUN_COMPLETE,
        }

    def test_values_are_strings(self):
        assert AgentEventType.TOOL_USE_START == "tool_use_start"
        assert AgentEventType.TOOL_RESULT == "tool_result"
        assert AgentEventType.TEXT_DELTA == "text_delta"
        assert AgentEventType.RUN_COMPLETE == "run_complete"


class TestAgentEvent:
    def test_fields(self):
        event = AgentEvent(type=AgentEventType.TEXT_DELTA, text="hello")
        assert event.type == AgentEventType.TEXT_DELTA
        assert event.text == "hello"
        assert event.tool_name is None
        assert event.tool_input is None
        assert event.tool_output is None

    def test_tool_use_event(self):
        event = AgentEvent(
            type=AgentEventType.TOOL_USE_START,
            tool_name="brave_search",
            tool_input={"query": "AAPL revenue"},
        )
        assert event.tool_name == "brave_search"
        assert event.tool_input == {"query": "AAPL revenue"}

    def test_tool_result_event(self):
        event = AgentEvent(
            type=AgentEventType.TOOL_RESULT,
            tool_name="brave_search",
            tool_output=[{"title": "Apple Revenue"}],
        )
        assert event.tool_output == [{"title": "Apple Revenue"}]


class TestAgentTool:
    def test_fields(self):
        async def dummy(**kwargs: Any) -> str:
            return "ok"

        tool = AgentTool(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {"q": {"type": "string"}}},
            fn=dummy,
        )
        assert tool.name == "test_tool"
        assert tool.description == "A test tool"
        assert "properties" in tool.parameters
        assert callable(tool.fn)


class TestAgentConnectorABC:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            AgentConnector()

    def test_run_stream_is_abstract(self):
        assert hasattr(AgentConnector, "run_stream")


# ── Claude Agent SDK implementation tests ────────────────────────


def _make_text_block(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_tool_use_block(name: str, tool_input: dict, tool_use_id: str = "tool_1") -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = f"mcp__analysis__{name}"
    block.input = tool_input
    block.id = tool_use_id
    return block


def _make_assistant_message(content: list) -> MagicMock:
    msg = MagicMock()
    msg.type = "assistant"
    msg.content = content
    # hasattr checks for AssistantMessage
    msg.__class__.__name__ = "AssistantMessage"
    return msg


def _make_result_message(result: str = "", subtype: str = "success") -> MagicMock:
    msg = MagicMock()
    msg.type = "result"
    msg.subtype = subtype
    msg.result = result
    msg.__class__.__name__ = "ResultMessage"
    return msg


def _make_system_message(subtype: str = "init") -> MagicMock:
    msg = MagicMock()
    msg.type = "system"
    msg.subtype = subtype
    msg.data = {"mcp_servers": []}
    msg.__class__.__name__ = "SystemMessage"
    return msg


class TestClaudeAgentConnector:
    def test_is_subclass(self):
        assert issubclass(ClaudeAgentConnector, AgentConnector)

    @pytest.mark.asyncio
    async def test_run_stream_yields_text_delta(self):
        """Agent returns text-only response (no tool calls)."""
        messages = [
            _make_system_message(),
            _make_assistant_message([_make_text_block("Apple's revenue grew 8%.")]),
            _make_result_message("Apple's revenue grew 8%."),
        ]

        events = await _run_with_messages(messages, tools=[])
        text_events = [e for e in events if e.type == AgentEventType.TEXT_DELTA]
        complete_events = [e for e in events if e.type == AgentEventType.RUN_COMPLETE]

        assert len(text_events) == 1
        assert text_events[0].text == "Apple's revenue grew 8%."
        assert len(complete_events) == 1

    @pytest.mark.asyncio
    async def test_run_stream_yields_tool_use_start(self):
        """Agent calls a tool — verify TOOL_USE_START event emitted."""
        messages = [
            _make_system_message(),
            _make_assistant_message(
                [
                    _make_tool_use_block("brave_search", {"query": "AAPL revenue"}),
                ]
            ),
            _make_assistant_message([_make_text_block("Based on my search...")]),
            _make_result_message("Based on my search..."),
        ]

        tool = _make_agent_tool("brave_search", return_value=[{"title": "AAPL Q4"}])
        events = await _run_with_messages(messages, tools=[tool])

        tool_starts = [e for e in events if e.type == AgentEventType.TOOL_USE_START]
        assert len(tool_starts) == 1
        assert tool_starts[0].tool_name == "brave_search"
        assert tool_starts[0].tool_input == {"query": "AAPL revenue"}

    @pytest.mark.asyncio
    async def test_run_stream_yields_tool_result(self):
        """After tool executes, TOOL_RESULT event is emitted with captured output."""
        search_result = [{"title": "AAPL Q4", "url": "https://example.com"}]

        messages = [
            _make_system_message(),
            _make_assistant_message(
                [
                    _make_tool_use_block("brave_search", {"query": "AAPL revenue"}),
                ]
            ),
            _make_assistant_message([_make_text_block("Analysis...")]),
            _make_result_message("Analysis..."),
        ]

        tool = _make_agent_tool("brave_search", return_value=search_result)
        events = await _run_with_messages(messages, tools=[tool])

        tool_results = [e for e in events if e.type == AgentEventType.TOOL_RESULT]
        assert len(tool_results) == 1
        assert tool_results[0].tool_name == "brave_search"
        assert tool_results[0].tool_output == search_result

    @pytest.mark.asyncio
    async def test_run_stream_multiple_tool_calls(self):
        """Agent calls multiple tools across turns."""
        messages = [
            _make_system_message(),
            _make_assistant_message(
                [
                    _make_tool_use_block("brave_search", {"query": "AAPL"}, "t1"),
                    _make_tool_use_block("get_company_profile", {"ticker": "AAPL"}, "t2"),
                ]
            ),
            _make_assistant_message([_make_text_block("Complete analysis.")]),
            _make_result_message("Complete analysis."),
        ]

        tools = [
            _make_agent_tool("brave_search", return_value=[{"title": "result"}]),
            _make_agent_tool("get_company_profile", return_value={"name": "Apple"}),
        ]

        events = await _run_with_messages(messages, tools=tools)

        tool_starts = [e for e in events if e.type == AgentEventType.TOOL_USE_START]
        assert len(tool_starts) == 2
        assert tool_starts[0].tool_name == "brave_search"
        assert tool_starts[1].tool_name == "get_company_profile"

    @pytest.mark.asyncio
    async def test_run_stream_respects_max_turns(self):
        """max_turns is passed to ClaudeAgentOptions."""
        messages = [
            _make_system_message(),
            _make_assistant_message([_make_text_block("ok")]),
            _make_result_message("ok"),
        ]

        with patch("connectors.claude_agent_connector.query") as mock_query:
            mock_query.return_value = _async_iter(messages)

            connector = ClaudeAgentConnector(api_key="test-key")
            events = []
            async for event in connector.run_stream(
                system_prompt="test",
                messages=[{"role": "user", "content": "test"}],
                tools=[],
                max_turns=5,
            ):
                events.append(event)

            call_kwargs = mock_query.call_args
            options = call_kwargs[1].get("options") or call_kwargs.kwargs.get("options")
            assert options.max_turns == 5

    @pytest.mark.asyncio
    async def test_run_stream_passes_system_prompt(self):
        """System prompt is forwarded to ClaudeAgentOptions."""
        messages = [
            _make_system_message(),
            _make_assistant_message([_make_text_block("ok")]),
            _make_result_message("ok"),
        ]

        with patch("connectors.claude_agent_connector.query") as mock_query:
            mock_query.return_value = _async_iter(messages)

            connector = ClaudeAgentConnector(api_key="test-key")
            events = []
            async for event in connector.run_stream(
                system_prompt="You are a financial analyst",
                messages=[{"role": "user", "content": "test"}],
                tools=[],
            ):
                events.append(event)

            call_kwargs = mock_query.call_args
            options = call_kwargs[1].get("options") or call_kwargs.kwargs.get("options")
            assert options.system_prompt == "You are a financial analyst"

    @pytest.mark.asyncio
    async def test_run_stream_registers_tools_as_mcp_server(self):
        """AgentTool list is registered via create_sdk_mcp_server."""
        messages = [
            _make_system_message(),
            _make_assistant_message([_make_text_block("ok")]),
            _make_result_message("ok"),
        ]

        tool = _make_agent_tool("brave_search", return_value=[])

        with (
            patch("connectors.claude_agent_connector.query") as mock_query,
            patch("connectors.claude_agent_connector.create_sdk_mcp_server") as mock_create,
        ):
            mock_query.return_value = _async_iter(messages)
            mock_create.return_value = MagicMock()

            connector = ClaudeAgentConnector(api_key="test-key")
            async for _ in connector.run_stream(
                system_prompt="test",
                messages=[{"role": "user", "content": "test"}],
                tools=[tool],
            ):
                pass

            mock_create.assert_called_once()
            create_kwargs = mock_create.call_args
            assert create_kwargs[1]["name"] == "analysis"
            assert len(create_kwargs[1]["tools"]) == 1

    @pytest.mark.asyncio
    async def test_run_stream_event_ordering(self):
        """Events come in correct order: TOOL_USE_START -> TOOL_RESULT -> TEXT_DELTA -> RUN_COMPLETE."""
        messages = [
            _make_system_message(),
            _make_assistant_message(
                [
                    _make_tool_use_block("brave_search", {"query": "test"}),
                ]
            ),
            _make_assistant_message([_make_text_block("Answer based on search.")]),
            _make_result_message("Answer based on search."),
        ]

        tool = _make_agent_tool("brave_search", return_value=[{"title": "result"}])
        events = await _run_with_messages(messages, tools=[tool])

        event_types = [e.type for e in events]
        assert event_types == [
            AgentEventType.TOOL_USE_START,
            AgentEventType.TOOL_RESULT,
            AgentEventType.TEXT_DELTA,
            AgentEventType.RUN_COMPLETE,
        ]

    @pytest.mark.asyncio
    async def test_run_stream_skips_system_messages(self):
        """SystemMessage (init) is not emitted as an AgentEvent."""
        messages = [
            _make_system_message(),
            _make_assistant_message([_make_text_block("ok")]),
            _make_result_message("ok"),
        ]

        events = await _run_with_messages(messages, tools=[])
        assert all(e.type != "system" for e in events)


# ── Helpers ──────────────────────────────────────────────────────


def _make_agent_tool(name: str, return_value: Any = None) -> AgentTool:
    async def fn(**kwargs: Any) -> Any:
        return return_value

    return AgentTool(
        name=name,
        description=f"Tool: {name}",
        parameters={"type": "object", "properties": {}},
        fn=fn,
    )


async def _async_iter(items):
    for item in items:
        yield item


async def _run_with_messages(
    messages: list,
    tools: list[AgentTool],
    system_prompt: str = "You are helpful",
    max_turns: int = 10,
) -> list[AgentEvent]:
    tool_map = {t.name: t for t in tools}
    captured_capture_list: list | None = None

    import connectors.claude_agent_connector as mod

    original_build = mod._build_mcp_tools

    def hooked_build(agent_tools, results_capture):
        nonlocal captured_capture_list
        captured_capture_list = results_capture
        return original_build(agent_tools, results_capture)

    async def simulated_query(**kwargs):
        for msg in messages:
            if getattr(msg, "type", None) == "assistant":
                for block in getattr(msg, "content", []):
                    if getattr(block, "type", None) == "tool_use":
                        short_name = block.name.replace("mcp__analysis__", "")
                        if short_name in tool_map and captured_capture_list is not None:
                            result = await tool_map[short_name].fn(**block.input)
                            captured_capture_list.append((short_name, result))
            yield msg

    with (
        patch.object(mod, "query", side_effect=simulated_query),
        patch.object(mod, "create_sdk_mcp_server", return_value=MagicMock()),
        patch.object(mod, "_build_mcp_tools", side_effect=hooked_build),
    ):
        connector = ClaudeAgentConnector(api_key="test-key")
        events = []
        async for event in connector.run_stream(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": "test"}],
            tools=tools,
            max_turns=max_turns,
        ):
            events.append(event)
        return events
