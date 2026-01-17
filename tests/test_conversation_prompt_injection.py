"""
Unit tests for conversation context injection into prompts.

Tests verify that:
- Conversation context is formatted correctly
- Context is injected into prompts for CompanyGeneralHandler
- Context is injected into prompts for CompanySpecificFinanceHandler
- Context is NOT injected when conversation_messages is None/empty
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.question_analyzer.company_specific_finance_handler import CompanySpecificFinanceHandler
from services.question_analyzer.handlers import CompanyGeneralHandler


class TestConversationContextFormatting:
    """Test conversation context formatting."""

    def test_format_conversation_context_with_messages(self):
        """Test formatting conversation context with messages."""
        from utils.conversation_format import format_conversation_context

        messages = [
            {"role": "user", "content": "What is Apple's profit margin?"},
            {"role": "assistant", "content": "Apple's profit margin is..."},
            {"role": "user", "content": "How does it compare to competitors?"},
            {"role": "assistant", "content": "Compared to competitors..."},
        ]

        result = format_conversation_context(messages, "AAPL", "Apple Inc.")

        assert "Apple Inc." in result
        assert "AAPL" in result
        assert "What is Apple's profit margin?" in result
        assert "How does it compare to competitors?" in result

    def test_format_conversation_context_empty(self):
        """Test formatting empty conversation context."""
        from utils.conversation_format import format_conversation_context

        result = format_conversation_context([], "AAPL", "Apple Inc.")

        assert result == ""

    def test_format_conversation_context_pinned_state(self):
        """Test that pinned state (company/ticker) is included."""
        from utils.conversation_format import format_conversation_context

        messages = [{"role": "user", "content": "Question"}]

        result = format_conversation_context(messages, "AAPL", "Apple Inc.")

        assert "Apple Inc." in result
        assert "AAPL" in result
        assert "Current conversation context" in result


class TestCompanyGeneralHandlerPromptInjection:
    """Test conversation context injection in CompanyGeneralHandler."""

    @pytest.mark.asyncio
    @patch("services.question_analyzer.handlers.MultiAgent")
    @patch("services.question_analyzer.handlers.CompanyConnector")
    async def test_prompt_includes_conversation_context(self, mock_connector_class, mock_agent_class):
        """Test that prompt includes conversation context when provided."""
        # Setup mocks
        mock_company = MagicMock()
        mock_company.name = "Apple Inc."
        mock_connector = MagicMock()
        mock_connector.get_by_ticker.return_value = mock_company
        mock_connector_class.return_value = mock_connector

        mock_agent_instance = MagicMock()
        mock_agent_instance.model_name = "test-model"
        mock_agent_instance.generate_content = AsyncMock(return_value=iter(["Answer chunk"]))
        mock_agent_class.return_value = mock_agent_instance

        handler = CompanyGeneralHandler(company_connector=mock_connector)

        conversation_messages = [
            {"role": "user", "content": "What is Apple's profit margin?"},
            {"role": "assistant", "content": "Apple's profit margin is 25%."},
        ]

        # Collect prompt from handler
        prompt_captured = None

        async def capture_prompt(*args, **kwargs):
            nonlocal prompt_captured
            prompt_captured = args[0] if args else kwargs.get("prompt", "")
            yield "Answer chunk"

        mock_agent_instance.generate_content = AsyncMock(side_effect=capture_prompt)

        # Call handler
        chunks = []
        async for chunk in handler.handle(
            ticker="AAPL",
            question="When was the company founded?",
            use_google_search=False,
            use_url_context=False,
            conversation_messages=conversation_messages,
        ):
            chunks.append(chunk)

        # Verify prompt includes conversation context
        assert prompt_captured is not None
        assert "Apple Inc." in prompt_captured
        assert "AAPL" in prompt_captured
        assert "What is Apple's profit margin?" in prompt_captured or "Previous conversation" in prompt_captured

    @pytest.mark.asyncio
    @patch("services.question_analyzer.handlers.MultiAgent")
    @patch("services.question_analyzer.handlers.CompanyConnector")
    async def test_prompt_excludes_context_when_none(self, mock_connector_class, mock_agent_class):
        """Test that prompt does not include conversation context when None."""
        mock_company = MagicMock()
        mock_company.name = "Apple Inc."
        mock_connector = MagicMock()
        mock_connector.get_by_ticker.return_value = mock_company
        mock_connector_class.return_value = mock_connector

        mock_agent_instance = MagicMock()
        mock_agent_instance.model_name = "test-model"
        mock_agent_instance.generate_content = AsyncMock(return_value=iter(["Answer chunk"]))
        mock_agent_class.return_value = mock_agent_instance

        handler = CompanyGeneralHandler(company_connector=mock_connector)

        prompt_captured = None

        async def capture_prompt(*args, **kwargs):
            nonlocal prompt_captured
            prompt_captured = args[0] if args else kwargs.get("prompt", "")
            yield "Answer chunk"

        mock_agent_instance.generate_content = AsyncMock(side_effect=capture_prompt)

        # Call handler without conversation_messages
        chunks = []
        async for chunk in handler.handle(
            ticker="AAPL",
            question="When was the company founded?",
            use_google_search=False,
            use_url_context=False,
            conversation_messages=None,
        ):
            chunks.append(chunk)

        # Verify prompt does NOT include conversation context markers
        assert prompt_captured is not None
        # Should not have "Previous conversation" when no context
        assert "Previous conversation" not in prompt_captured


class TestCompanySpecificFinanceHandlerPromptInjection:
    """Test conversation context injection in CompanySpecificFinanceHandler."""

    @pytest.mark.asyncio
    @patch("services.question_analyzer.company_specific_finance_handler.MultiAgent")
    @patch("services.question_analyzer.company_specific_finance_handler.QuestionClassifier")
    @patch("services.question_analyzer.company_specific_finance_handler.FinancialDataOptimizer")
    @patch("services.question_analyzer.company_specific_finance_handler.CompanyConnector")
    async def test_prompt_includes_conversation_context(
        self, mock_connector_class, mock_optimizer_class, mock_classifier_class, mock_agent_class
    ):
        """Test that prompt includes conversation context when provided."""
        from services.question_analyzer.types import FinancialDataRequirement

        # Setup mocks
        mock_classifier = MagicMock()
        mock_classifier.classify_data_requirement = AsyncMock(return_value=FinancialDataRequirement.BASIC)
        mock_classifier_class.return_value = mock_classifier

        mock_optimizer = MagicMock()
        mock_optimizer.fetch_optimized_data = AsyncMock(
            return_value=(
                {"name": "Apple Inc."},  # company_fundamental
                [],  # annual_statements
                [],  # quarterly_statements
            )
        )
        mock_optimizer_class.return_value = mock_optimizer

        mock_agent_instance = MagicMock()
        mock_agent_instance.model_name = "test-model"
        mock_agent_class.return_value = mock_agent_instance

        handler = CompanySpecificFinanceHandler(classifier=mock_classifier, data_optimizer=mock_optimizer)

        conversation_messages = [
            {"role": "user", "content": "What is Apple's profit margin?"},
            {"role": "assistant", "content": "Apple's profit margin is 25%."},
        ]

        prompt_captured = None

        async def capture_prompt(*args, **kwargs):
            nonlocal prompt_captured
            prompt_captured = args[0] if args else kwargs.get("prompt", "")
            yield "Answer chunk"

        mock_agent_instance.generate_content = AsyncMock(side_effect=capture_prompt)

        # Call handler
        chunks = []
        async for chunk in handler.handle(
            ticker="AAPL",
            question="How is revenue trending?",
            use_google_search=False,
            use_url_context=False,
            conversation_messages=conversation_messages,
        ):
            chunks.append(chunk)
            # Break early after we capture the prompt
            if len(chunks) > 5:
                break

        # Verify prompt includes conversation context
        assert prompt_captured is not None
        assert "AAPL" in prompt_captured or "Apple Inc." in prompt_captured
        # Should include conversation context markers
        assert (
            "Previous conversation" in prompt_captured
            or "Context:" in prompt_captured
            or "What is Apple's profit margin?" in prompt_captured
        )
