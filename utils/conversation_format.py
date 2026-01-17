"""
Utility for formatting conversation history into prompt context.
"""

from typing import List


def format_conversation_context(messages: List[dict], ticker: str, company_name: str = "") -> str:
    """
    Format conversation history into a concise context block for prompt injection.

    Args:
        messages: List of message dictionaries with 'role' and 'content'
        ticker: Company ticker symbol
        company_name: Company name (optional, defaults to ticker if not provided)

    Returns:
        Formatted conversation context string
    """
    if not messages:
        return ""

    # Build pinned state
    company_display = company_name if company_name else ticker.upper()
    pinned_state = f"Current conversation context:\n- Company: {company_display} ({ticker.upper()})\n"

    # Format recent Q/A pairs
    conversation_lines = []
    for msg in messages:
        role = msg.get("role", "").upper()
        content = msg.get("content", "").strip()
        if content:
            conversation_lines.append(f"{role}: {content}")

    if conversation_lines:
        conversation_block = "\n".join(conversation_lines)
        return f"{pinned_state}\nPrevious conversation:\n{conversation_block}\n"

    return pinned_state


def format_conversation_context_minimal(messages: List[dict], ticker: str, company_name: str = "") -> str:
    """
    Format conversation history into a minimal context block (just pinned state + last few turns).

    This is a lighter version that focuses on the most recent context.

    Args:
        messages: List of message dictionaries with 'role' and 'content'
        ticker: Company ticker symbol
        company_name: Company name (optional)

    Returns:
        Formatted conversation context string
    """
    if not messages:
        return ""

    company_display = company_name if company_name else ticker.upper()
    context = f"Context: You are discussing {company_display} ({ticker.upper()}).\n"

    # Only include the last Q/A pair for minimal context
    if len(messages) >= 2:
        last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
        last_assistant = next((m for m in reversed(messages) if m.get("role") == "assistant"), None)

        if last_user and last_assistant:
            context += f"Previous question: {last_user.get('content', '')}\n"
            context += f"Previous answer summary: {last_assistant.get('content', '')[:200]}...\n"

    return context
