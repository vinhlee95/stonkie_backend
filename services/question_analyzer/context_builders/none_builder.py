"""Context builder for NONE financial data requirement."""

from .base import ContextBuilder, ContextBuilderInput
from .components import PromptComponents


class NoneContextBuilder(ContextBuilder):
    """Builds context for questions that don't require financial data."""

    def build(self, input: ContextBuilderInput) -> str:
        """Build context for general company questions without financial data."""
        base_context = PromptComponents.base_context(input.ticker, input.question)

        return f"""
            {base_context}

            {PromptComponents.grounding_rules()}

            This is a general question about {input.ticker.upper()}.
            Keep the response under 150 words and make it engaging.
        """
