"""Context builder for basic ETF information questions."""

import json

from .base import ETFContextBuilder, ETFContextBuilderInput
from .components import ETFPromptComponents


class BasicETFBuilder(ETFContextBuilder):
    """Builds context for ETF overview questions using core metadata."""

    def build(self, input: ETFContextBuilderInput) -> str:
        """Build context for basic ETF info questions."""
        base_context = ETFPromptComponents.base_context(input.ticker, input.question)
        source_instructions = ETFPromptComponents.source_instructions()
        formatting = ETFPromptComponents.etf_data_formatting()

        # Extract core metadata
        etf_metadata = {}
        if input.etf_data:
            etf_metadata = {
                "name": input.etf_data.name,
                "isin": input.etf_data.isin,
                "ticker": input.etf_data.ticker,
                "fund_provider": input.etf_data.fund_provider,
                "ter_percent": input.etf_data.ter_percent,
                "fund_size_billions": input.etf_data.fund_size_billions,
                "replication_method": input.etf_data.replication_method,
                "distribution_policy": input.etf_data.distribution_policy,
                "fund_currency": input.etf_data.fund_currency,
                "domicile": input.etf_data.domicile,
                "launch_date": input.etf_data.launch_date,
                "index_tracked": input.etf_data.index_tracked,
            }

        # Check data completeness
        data_complete = bool(
            etf_metadata.get("name")
            and etf_metadata.get("ter_percent") is not None
            and etf_metadata.get("fund_provider")
        )

        incomplete_warning = ""
        if not data_complete:
            incomplete_warning = "\n\nNOTE: Some ETF data may be incomplete. Use Google Search to supplement if needed."

        return f"""
            {base_context}

            ETF Core Metadata:
            {json.dumps(etf_metadata, indent=2)}

            Answer using the ETF metadata provided.
            Keep response under 150 words.
            {incomplete_warning}

            {formatting}
            {source_instructions}
        """
