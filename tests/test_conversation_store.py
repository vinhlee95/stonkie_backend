"""
Unit tests for conversation store (Redis-backed conversation memory).

Tests focus on:
- Redis key generation and scoping
- Message trimming to last 3 Q/A pairs
- Isolation across users/tickers/conversations
- Context retrieval for prompt injection
"""

import json
from unittest.mock import MagicMock, patch

from connectors.conversation_store import (
    append_user_message,
    generate_conversation_id,
    get_conversation_history_for_prompt,
    get_conversation_key,
    get_messages,
)


class TestConversationKeyGeneration:
    """Test Redis key generation for conversation storage."""

    def test_get_conversation_key_format(self):
        """Test that conversation keys are formatted correctly."""
        user_id = "user123"
        ticker = "AAPL"
        conversation_id = "conv456"

        key = get_conversation_key(user_id, ticker, conversation_id)

        assert key == "conversation:user123:AAPL:conv456"
        assert "conversation:" in key
        assert user_id in key
        assert ticker.upper() in key
        assert conversation_id in key

    def test_get_conversation_key_ticker_uppercase(self):
        """Test that ticker is always uppercase in key."""
        key1 = get_conversation_key("user1", "aapl", "conv1")
        key2 = get_conversation_key("user1", "AAPL", "conv1")

        assert key1 == key2
        assert "AAPL" in key1


class TestConversationIsolation:
    """Test that conversations are isolated across users/tickers/conversations."""

    @patch("connectors.conversation_store.redis_client")
    def test_isolation_by_user_id(self, mock_redis):
        """Test that different user IDs create different conversation keys."""
        user1 = "user1"
        user2 = "user2"
        ticker = "AAPL"
        conv_id = "conv1"

        key1 = get_conversation_key(user1, ticker, conv_id)
        key2 = get_conversation_key(user2, ticker, conv_id)

        assert key1 != key2
        assert user1 in key1
        assert user2 in key2

    @patch("connectors.conversation_store.redis_client")
    def test_isolation_by_ticker(self, mock_redis):
        """Test that different tickers create different conversation keys."""
        user_id = "user1"
        ticker1 = "AAPL"
        ticker2 = "MSFT"
        conv_id = "conv1"

        key1 = get_conversation_key(user_id, ticker1, conv_id)
        key2 = get_conversation_key(user_id, ticker2, conv_id)

        assert key1 != key2
        assert "AAPL" in key1
        assert "MSFT" in key2

    @patch("connectors.conversation_store.redis_client")
    def test_isolation_by_conversation_id(self, mock_redis):
        """Test that different conversation IDs create different keys."""
        user_id = "user1"
        ticker = "AAPL"
        conv1 = "conv1"
        conv2 = "conv2"

        key1 = get_conversation_key(user_id, ticker, conv1)
        key2 = get_conversation_key(user_id, ticker, conv2)

        assert key1 != key2
        assert conv1 in key1
        assert conv2 in key2


class TestMessageTrimming:
    """Test that messages are trimmed to last 3 Q/A pairs."""

    @patch("connectors.conversation_store.redis_client")
    def test_trimming_to_max_pairs(self, mock_redis):
        """Test that messages are trimmed to MAX_HISTORY_PAIRS (3 pairs = 6 messages)."""
        user_id = "user1"
        ticker = "AAPL"
        conv_id = "conv1"

        # Create 8 messages (4 Q/A pairs) - should trim to last 3 pairs (6 messages)
        messages = []
        for i in range(4):
            messages.append({"role": "user", "content": f"Question {i}"})
            messages.append({"role": "assistant", "content": f"Answer {i}"})

        mock_redis.get.return_value = json.dumps(messages).encode()
        mock_redis.setex = MagicMock()

        # Get messages (should trim to last 3 pairs)
        result = get_conversation_history_for_prompt(user_id, ticker, conv_id, max_pairs=3)

        # Should have exactly 6 messages (3 pairs)
        assert len(result) == 6
        # Should start with Question 1 (not Question 0)
        assert result[0]["content"] == "Question 1"
        assert result[-1]["content"] == "Answer 3"

    @patch("connectors.conversation_store.redis_client")
    def test_no_trimming_when_under_limit(self, mock_redis):
        """Test that messages are not trimmed when under the limit."""
        user_id = "user1"
        ticker = "AAPL"
        conv_id = "conv1"

        # Create 4 messages (2 Q/A pairs) - should not trim
        messages = [
            {"role": "user", "content": "Question 1"},
            {"role": "assistant", "content": "Answer 1"},
            {"role": "user", "content": "Question 2"},
            {"role": "assistant", "content": "Answer 2"},
        ]

        mock_redis.get.return_value = json.dumps(messages).encode()

        result = get_conversation_history_for_prompt(user_id, ticker, conv_id, max_pairs=3)

        assert len(result) == 4
        assert result[0]["content"] == "Question 1"
        assert result[-1]["content"] == "Answer 2"

    @patch("connectors.conversation_store.redis_client")
    def test_append_trims_before_storing(self, mock_redis):
        """Test that append_message trims before storing to Redis."""
        user_id = "user1"
        ticker = "AAPL"
        conv_id = "conv1"

        # Start with 6 messages (3 pairs)
        existing_messages = []
        for i in range(3):
            existing_messages.append({"role": "user", "content": f"Q{i}"})
            existing_messages.append({"role": "assistant", "content": f"A{i}"})

        mock_redis.get.return_value = json.dumps(existing_messages).encode()
        mock_redis.setex = MagicMock()

        # Append new user message (should trim to last 3 pairs before storing)
        append_user_message(user_id, ticker, conv_id, "New Question", trim_to_max_pairs=True)

        # Verify setex was called
        assert mock_redis.setex.called

        # Get the stored messages
        call_args = mock_redis.setex.call_args
        stored_messages_json = call_args[0][1]  # Second positional arg is the JSON value
        stored_messages = json.loads(stored_messages_json)

        # Should have exactly 6 messages (3 pairs) - oldest pair removed
        assert len(stored_messages) == 6
        # Should start with Q1 (not Q0)
        assert stored_messages[0]["content"] == "Q1"
        # Should end with new question
        assert stored_messages[-1]["content"] == "New Question"
        assert stored_messages[-1]["role"] == "user"


class TestContextRetrieval:
    """Test conversation context retrieval for prompt injection."""

    @patch("connectors.conversation_store.redis_client")
    def test_get_messages_empty_conversation(self, mock_redis):
        """Test getting messages from empty conversation."""
        user_id = "user1"
        ticker = "AAPL"
        conv_id = "conv1"

        mock_redis.get.return_value = None

        result = get_messages(user_id, ticker, conv_id)

        assert result == []

    @patch("connectors.conversation_store.redis_client")
    def test_get_messages_with_history(self, mock_redis):
        """Test getting messages from conversation with history."""
        user_id = "user1"
        ticker = "AAPL"
        conv_id = "conv1"

        messages = [
            {"role": "user", "content": "What is Apple's revenue?"},
            {"role": "assistant", "content": "Apple's revenue is..."},
        ]

        mock_redis.get.return_value = json.dumps(messages).encode()

        result = get_messages(user_id, ticker, conv_id)

        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    @patch("connectors.conversation_store.redis_client")
    def test_get_conversation_history_for_prompt_trims(self, mock_redis):
        """Test that get_conversation_history_for_prompt returns trimmed history."""
        user_id = "user1"
        ticker = "AAPL"
        conv_id = "conv1"

        # Create 8 messages (4 pairs)
        messages = []
        for i in range(4):
            messages.append({"role": "user", "content": f"Q{i}"})
            messages.append({"role": "assistant", "content": f"A{i}"})

        mock_redis.get.return_value = json.dumps(messages).encode()

        result = get_conversation_history_for_prompt(user_id, ticker, conv_id, max_pairs=3)

        # Should return last 3 pairs (6 messages)
        assert len(result) == 6
        assert result[0]["content"] == "Q1"
        assert result[-1]["content"] == "A3"


class TestConversationIdGeneration:
    """Test conversation ID generation."""

    def test_generate_conversation_id_is_uuid(self):
        """Test that generated conversation IDs are valid UUIDs."""
        import uuid

        conv_id = generate_conversation_id()

        # Should be a valid UUID string
        uuid.UUID(conv_id)  # Will raise ValueError if invalid

    def test_generate_conversation_id_unique(self):
        """Test that generated conversation IDs are unique."""
        ids = {generate_conversation_id() for _ in range(100)}

        # All 100 IDs should be unique
        assert len(ids) == 100
