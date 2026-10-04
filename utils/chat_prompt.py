"""Prompt and tracing helpers shared by the streaming chat services."""

import re


def format_conversation(messages: list[dict[str, str]] | None, limit: int = 6) -> str:
    """The last `limit` messages as "ROLE: text" lines for a prompt, or "" when there are none."""
    lines = []
    for msg in (messages or [])[-limit:]:
        role = (msg.get("role") or "").upper()
        content = re.sub(r"\s+", " ", msg.get("content") or "").strip()
        if role and content:
            lines.append(f"{role}: {content}")
    return "Recent conversation:\n" + "\n".join(lines) if lines else ""


def extract_answer_text(chunks: list) -> str:
    """Langfuse transform: the answer text out of a list of streamed events."""
    return "".join(c.get("body", "") for c in chunks if isinstance(c, dict) and c.get("type") == "answer")
