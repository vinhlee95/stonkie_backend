"""
Unit tests for conversation context injection into v2 analyze prompts.

Tests verify that:
- Conversation context is formatted correctly
- Context is injected into prompts for CompanyGeneralHandlerV2
- Context is injected into prompts for CompanySpecificFinanceHandlerV2
- Context is NOT injected when conversation_messages is None/empty
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.question_analyzer.handlers_v2 import CompanyGeneralHandlerV2, CompanySpecificFinanceHandlerV2
from services.question_analyzer.types import FinancialDataRequirement, QuestionType
from tests.services.routing_fakes import make_routing
from utils.conversation_format import format_conversation_context

_HISTORY = [
    {"role": "user", "content": "What is Apple's profit margin?"},
    {"role": "assistant", "content": "Apple's profit margin is 25%."},
]


async def _no_related(*_a, **_k):
    if False:
        yield {}


def _capturing_agent() -> tuple[MagicMock, dict]:
    captured: dict = {}

    def capture_prompt(*_args, **kwargs):
        captured["prompt"] = kwargs.get("prompt", "")
        yield "Answer chunk"

    agent = MagicMock()
    agent.model_name = "test-model"
    agent.generate_content = MagicMock(side_effect=capture_prompt)
    return agent, captured


class TestConversationContextFormatting:
    """Test conversation context formatting."""

    def test_format_conversation_context_with_messages(self):
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
        assert format_conversation_context([], "AAPL", "Apple Inc.") == ""

    def test_format_conversation_context_pinned_state(self):
        result = format_conversation_context([{"role": "user", "content": "Question"}], "AAPL", "Apple Inc.")

        assert "Apple Inc." in result
        assert "AAPL" in result
        assert "Current conversation context" in result


class TestCompanyGeneralHandlerV2PromptInjection:
    """Test conversation context injection in CompanyGeneralHandlerV2."""

    async def _prompt_for(self, conversation_messages) -> str:
        company = MagicMock()
        company.name = "Apple Inc."
        company.country = "United States"
        connector = MagicMock()
        connector.get_by_ticker.return_value = company
        agent, captured = _capturing_agent()

        handler = CompanyGeneralHandlerV2(company_connector=connector)
        handler._generate_related_questions = _no_related  # type: ignore[method-assign]
        routing = make_routing(standalone_question="When was Apple founded?")
        with patch("services.question_analyzer.handlers_v2.MultiAgent", return_value=agent):
            async for _ in handler.handle(
                ticker="AAPL",
                question="When was the company founded?",
                search_decision=routing.search_decision,
                use_url_context=False,
                conversation_messages=conversation_messages,
                routing=routing,
            ):
                pass
        return captured["prompt"]

    @pytest.mark.asyncio
    async def test_prompt_includes_conversation_context(self):
        prompt = await self._prompt_for(_HISTORY)

        assert "Apple Inc." in prompt
        assert "AAPL" in prompt
        assert "What is Apple's profit margin?" in prompt

    @pytest.mark.asyncio
    async def test_prompt_excludes_context_when_none(self):
        prompt = await self._prompt_for(None)

        assert "What is Apple's profit margin?" not in prompt
        assert "Current conversation context" not in prompt


class TestCompanySpecificFinanceHandlerV2PromptInjection:
    """Test conversation context injection in CompanySpecificFinanceHandlerV2."""

    @pytest.mark.asyncio
    async def test_prompt_includes_conversation_context(self):
        optimizer = MagicMock()
        optimizer.fetch_optimized_data = AsyncMock(return_value=({"Name": "Apple Inc."}, [], []))
        agent, captured = _capturing_agent()

        handler = CompanySpecificFinanceHandlerV2(company_connector=MagicMock(), data_optimizer=optimizer)
        handler._generate_related_questions = _no_related  # type: ignore[method-assign]
        routing = make_routing(
            QuestionType.COMPANY_SPECIFIC_FINANCE.value,
            data_requirement=FinancialDataRequirement.BASIC,
            standalone_question="How is Apple's revenue trending?",
        )
        with patch("services.question_analyzer.handlers_v2.MultiAgent", return_value=agent):
            async for _ in handler.handle(
                ticker="AAPL",
                question="How is revenue trending?",
                search_decision=routing.search_decision,
                use_url_context=False,
                conversation_messages=_HISTORY,
                routing=routing,
            ):
                pass

        prompt = captured["prompt"]
        assert "AAPL" in prompt
        assert "What is Apple's profit margin?" in prompt
        assert "Current conversation context" in prompt
