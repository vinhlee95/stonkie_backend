"""
Redis-backed conversation store for managing chat history per user/ticker/conversation.
"""

import json
import logging
import uuid
from typing import List

from connectors.cache import redis_client

logger = logging.getLogger(__name__)

# TTL for conversation data (15 minutes = 900 seconds)
CONVERSATION_TTL = 900

# Maximum number of Q/A pairs to keep in history (for prompt injection)
MAX_HISTORY_PAIRS = 3


def get_conversation_key(user_id: str, ticker: str, conversation_id: str) -> str:
    """
    Generate Redis key for conversation storage.

    Args:
        user_id: Anonymous user ID from cookie
        ticker: Company ticker symbol
        conversation_id: Unique conversation identifier

    Returns:
        Redis key string
    """
    return f"conversation:{user_id}:{ticker.upper()}:{conversation_id}"


def get_messages(user_id: str, ticker: str, conversation_id: str) -> List[dict]:
    """
    Get conversation messages from Redis.

    Args:
        user_id: Anonymous user ID from cookie
        ticker: Company ticker symbol
        conversation_id: Unique conversation identifier

    Returns:
        List of message dictionaries with 'role' ('user' or 'assistant') and 'content'
    """
    key = get_conversation_key(user_id, ticker, conversation_id)
    messages_json = redis_client.get(key)

    if messages_json:
        try:
            messages = json.loads(messages_json)
            messages_list = messages if isinstance(messages, list) else []
            logger.debug(
                f"📖 Retrieved {len(messages_list)} message(s) from Redis "
                f"(key: {key}, user: {user_id[:8]}..., ticker: {ticker.upper()})"
            )
            return messages_list
        except json.JSONDecodeError:
            logger.error(f"Failed to parse conversation messages from Redis key: {key}")
            return []

    logger.debug(f"📖 No messages found in Redis (key: {key}, user: {user_id[:8]}..., ticker: {ticker.upper()})")
    return []


def _trim_messages(messages: List[dict], max_pairs: int = MAX_HISTORY_PAIRS) -> List[dict]:
    """
    Trim messages to keep only the last N Q/A pairs.

    Args:
        messages: List of message dictionaries
        max_pairs: Maximum number of Q/A pairs to keep

    Returns:
        Trimmed list of messages
    """
    if len(messages) <= max_pairs * 2:  # Each pair = 2 messages (user + assistant)
        return messages

    # Keep only the last max_pairs * 2 messages
    return messages[-(max_pairs * 2) :]


def append_message(
    user_id: str,
    ticker: str,
    conversation_id: str,
    role: str,
    content: str,
    trim_to_max_pairs: bool = True,
) -> None:
    """
    Append a message to the conversation and store in Redis.

    Args:
        user_id: Anonymous user ID from cookie
        ticker: Company ticker symbol
        conversation_id: Unique conversation identifier
        role: Message role ('user' or 'assistant')
        content: Message content
        trim_to_max_pairs: Whether to trim to MAX_HISTORY_PAIRS before storing
    """
    key = get_conversation_key(user_id, ticker, conversation_id)

    # Get existing messages
    messages = get_messages(user_id, ticker, conversation_id)

    # Append new message
    messages.append({"role": role, "content": content})

    # Trim if requested (we trim before storing to keep Redis size manageable)
    if trim_to_max_pairs:
        messages = _trim_messages(messages, MAX_HISTORY_PAIRS)

    # Store back to Redis with TTL refresh
    redis_client.setex(key, CONVERSATION_TTL, json.dumps(messages))
    logger.debug(f"Stored message for conversation {conversation_id} (user: {user_id}, ticker: {ticker})")


def append_user_message(
    user_id: str, ticker: str, conversation_id: str, content: str, trim_to_max_pairs: bool = True
) -> None:
    """
    Append a user message to the conversation.

    Args:
        user_id: Anonymous user ID from cookie
        ticker: Company ticker symbol
        conversation_id: Unique conversation identifier
        content: User message content
        trim_to_max_pairs: Whether to trim to MAX_HISTORY_PAIRS before storing
    """
    append_message(user_id, ticker, conversation_id, "user", content, trim_to_max_pairs)


def append_assistant_message(
    user_id: str, ticker: str, conversation_id: str, content: str, trim_to_max_pairs: bool = True
) -> None:
    """
    Append an assistant message to the conversation.

    Args:
        user_id: Anonymous user ID from cookie
        ticker: Company ticker symbol
        conversation_id: Unique conversation identifier
        content: Assistant message content
        trim_to_max_pairs: Whether to trim to MAX_HISTORY_PAIRS before storing
    """
    append_message(user_id, ticker, conversation_id, "assistant", content, trim_to_max_pairs)


def get_conversation_history_for_prompt(
    user_id: str, ticker: str, conversation_id: str, max_pairs: int = MAX_HISTORY_PAIRS
) -> List[dict]:
    """
    Get conversation history formatted for prompt injection (trimmed to max_pairs).

    Args:
        user_id: Anonymous user ID from cookie
        ticker: Company ticker symbol
        conversation_id: Unique conversation identifier
        max_pairs: Maximum number of Q/A pairs to return

    Returns:
        List of message dictionaries (trimmed to last max_pairs)
    """
    messages = get_messages(user_id, ticker, conversation_id)
    trimmed = _trim_messages(messages, max_pairs)

    if trimmed and len(messages) > len(trimmed):
        logger.debug(
            f"✂️  Trimmed conversation history from {len(messages)} to {len(trimmed)} messages "
            f"(keeping last {max_pairs} Q/A pairs)"
        )

    return trimmed


def generate_conversation_id() -> str:
    """
    Generate a new conversation ID.

    Returns:
        UUID string
    """
    return str(uuid.uuid4())
