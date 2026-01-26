"""Context builder for general ETF education questions."""

from .base import ETFContextBuilder, ETFContextBuilderInput
from .components import ETFPromptComponents


class NoneETFBuilder(ETFContextBuilder):
    """Builds context for general ETF questions without specific ETF data."""

    def build(self, input: ETFContextBuilderInput) -> str:
        """Build context for general ETF education questions."""
        base_context = ETFPromptComponents.base_context(input.ticker, input.question)
        source_instructions = ETFPromptComponents.source_instructions()

        return f"""
            {base_context}

            This is a general ETF education question.
            Provide clear explanation using ETF terminology.
            Keep response under 150 words.
            Use Google Search if needed for current information.

            {source_instructions}
        """
