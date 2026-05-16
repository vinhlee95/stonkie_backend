"""Provider-agnostic agent connector interface.

The service layer depends ONLY on this interface — never on a specific SDK.
Swap implementations (Claude, OpenAI, etc.) without touching business logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, AsyncGenerator, Awaitable, Callable


class AgentEventType(StrEnum):
    TOOL_USE_START = "tool_use_start"
    TOOL_RESULT = "tool_result"
    TEXT_DELTA = "text_delta"
    RUN_COMPLETE = "run_complete"


@dataclass
class AgentEvent:
    type: AgentEventType
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_output: Any = None
    text: str | None = None


@dataclass
class AgentTool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., Awaitable[Any]]


class AgentConnector(ABC):
    @abstractmethod
    async def run_stream(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[AgentTool],
        max_turns: int = 10,
    ) -> AsyncGenerator[AgentEvent, None]:
        raise NotImplementedError
        yield  # noqa: F821 — makes this an async generator
