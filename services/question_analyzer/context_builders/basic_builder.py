"""Context builder for BASIC financial data requirement."""

from .base import ContextBuilder, ContextBuilderInput
from .components import PromptComponents


class BasicContextBuilder(ContextBuilder):
    """Builds context for questions requiring basic financial metrics."""

    def build(self, input: ContextBuilderInput) -> str:
        """Build context using fundamental company data."""
        base_context = PromptComponents.base_context(input.ticker, input.question)

        return f"""
            {base_context}
            
            Company Fundamental Data:
            {input.company_fundamental}
            
            This question requires basic financial metrics. Use the fundamental data provided to answer the question.
            Focus on key metrics like market cap, P/E ratio, basic profitability, and market performance.
            Keep the response concise (under 150 words) but insightful.
            Use Google Search for additional context if needed.
        """
