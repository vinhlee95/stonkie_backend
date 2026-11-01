"""A function tool the answer model may call during OpenRouterClient.stream_chat."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class ChatToolResult:
    content: str  # sent to the model as the tool message
    meta: dict = field(default_factory=dict)  # for progress statuses (e.g. source count), never sent to the model


@dataclass(frozen=True)
class ChatTool:
    name: str
    description: str
    parameters: dict  # JSON schema of the arguments
    run: Callable[[dict], ChatToolResult]

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }
