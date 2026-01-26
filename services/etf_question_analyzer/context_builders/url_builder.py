"""Context builder for URL-based ETF analysis."""

import json

from .base import ETFContextBuilder, ETFContextBuilderInput
from .components import ETFPromptComponents


class UrlETFBuilder(ETFContextBuilder):
    """Builds context for analyzing ETF factsheets and prospectuses from URLs."""

    def build(self, input: ETFContextBuilderInput) -> str:
        """Build context for URL-based ETF analysis."""
        base_context = ETFPromptComponents.base_context(input.ticker, input.question)
        source_instructions = ETFPromptComponents.source_instructions()

        # Include database data if available
        etf_metadata = {}
        if input.etf_data:
            etf_metadata = {
                "name": input.etf_data.name,
                "ticker": input.etf_data.ticker,
                "ter_percent": input.etf_data.ter_percent,
                "fund_provider": input.etf_data.fund_provider,
            }

        metadata_context = ""
        if etf_metadata:
            metadata_context = f"\n\nDatabase ETF Data (for reference):\n{json.dumps(etf_metadata, indent=2)}"

        return f"""
            {base_context}

            Analyze the ETF document at: {input.source_url}
            {metadata_context}

            Extract key information:
            - Investment strategy and objectives
            - Costs and fees (TER, transaction costs)
            - Risk factors
            - Historical performance (if available)
            - Holdings or sector allocation (if available)

            Combine URL content with database data where applicable.
            MUST cite the URL as source.

            {source_instructions}
        """
