"""Claude Agent SDK implementation of AgentConnector."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncGenerator

from claude_agent_sdk import (
    ClaudeAgentOptions,
    create_sdk_mcp_server,
    query,
    tool,
)

from connectors.agent_connector import AgentConnector, AgentEvent, AgentEventType, AgentTool

logger = logging.getLogger(__name__)

MCP_SERVER_NAME = "analysis"


class ClaudeAgentConnector(AgentConnector):
    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-4-6-20250514"):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model

    async def run_stream(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[AgentTool],
        max_turns: int = 10,
    ) -> AsyncGenerator[AgentEvent, None]:
        tool_results_capture: list[tuple[str, Any]] = []

        mcp_tools = _build_mcp_tools(tools, tool_results_capture)
        mcp_server = (
            create_sdk_mcp_server(
                name=MCP_SERVER_NAME,
                version="1.0.0",
                tools=mcp_tools,
            )
            if mcp_tools
            else None
        )

        allowed = [f"mcp__{MCP_SERVER_NAME}__*"] if mcp_tools else []

        prompt = messages[-1]["content"] if messages else ""

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            max_turns=max_turns,
            tools=[],
            allowed_tools=allowed,
            permission_mode="bypassPermissions",
            **({"mcp_servers": {MCP_SERVER_NAME: mcp_server}} if mcp_server else {}),
        )

        pending_tool_uses: list[tuple[str, dict]] = []

        async for message in query(prompt=prompt, options=options):
            msg_type = getattr(message, "type", None)

            if msg_type == "system":
                continue

            if msg_type == "assistant":
                for block in getattr(message, "content", []):
                    block_type = getattr(block, "type", None)

                    if block_type == "tool_use":
                        raw_name = block.name
                        short_name = raw_name.replace(f"mcp__{MCP_SERVER_NAME}__", "")
                        yield AgentEvent(
                            type=AgentEventType.TOOL_USE_START,
                            tool_name=short_name,
                            tool_input=block.input,
                        )
                        pending_tool_uses.append((short_name, block.input))

                    elif block_type == "text" and getattr(block, "text", ""):
                        if pending_tool_uses and tool_results_capture:
                            for captured_name, captured_output in tool_results_capture:
                                yield AgentEvent(
                                    type=AgentEventType.TOOL_RESULT,
                                    tool_name=captured_name,
                                    tool_output=captured_output,
                                )
                            tool_results_capture.clear()
                            pending_tool_uses.clear()

                        yield AgentEvent(
                            type=AgentEventType.TEXT_DELTA,
                            text=block.text,
                        )

            elif msg_type == "result":
                if pending_tool_uses and tool_results_capture:
                    for captured_name, captured_output in tool_results_capture:
                        yield AgentEvent(
                            type=AgentEventType.TOOL_RESULT,
                            tool_name=captured_name,
                            tool_output=captured_output,
                        )
                    tool_results_capture.clear()
                    pending_tool_uses.clear()

                yield AgentEvent(type=AgentEventType.RUN_COMPLETE)


def _build_mcp_tools(
    agent_tools: list[AgentTool],
    results_capture: list[tuple[str, Any]],
) -> list:
    mcp_tools = []
    for at in agent_tools:
        mcp_tool = _wrap_agent_tool(at, results_capture)
        mcp_tools.append(mcp_tool)
    return mcp_tools


def _wrap_agent_tool(
    agent_tool: AgentTool,
    results_capture: list[tuple[str, Any]],
):
    @tool(agent_tool.name, agent_tool.description, agent_tool.parameters)
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        result = await agent_tool.fn(**args)
        results_capture.append((agent_tool.name, result))
        return {
            "content": [{"type": "text", "text": json.dumps(result, default=str)}],
        }

    return handler
