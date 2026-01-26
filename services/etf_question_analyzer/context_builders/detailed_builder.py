"""Context builder for detailed ETF analysis questions."""

import json

from .base import ETFContextBuilder, ETFContextBuilderInput
from .components import ETFPromptComponents


class DetailedETFBuilder(ETFContextBuilder):
    """Builds context for detailed ETF analysis using full data."""

    def build(self, input: ETFContextBuilderInput) -> str:
        """Build context for detailed ETF analysis questions."""
        base_context = ETFPromptComponents.base_context(input.ticker, input.question)
        source_instructions = ETFPromptComponents.source_instructions()
        holdings_format = ETFPromptComponents.holdings_formatting()
        sector_format = ETFPromptComponents.sector_formatting()
        incomplete_instructions = ETFPromptComponents.incomplete_data_instructions()

        # Build ETF data context
        etf_context = {}
        if input.etf_data:
            etf_context = {
                "name": input.etf_data.name,
                "ticker": input.etf_data.ticker,
                "ter_percent": input.etf_data.ter_percent,
                "fund_size_billions": input.etf_data.fund_size_billions,
                "index_tracked": input.etf_data.index_tracked,
                "holdings": input.etf_data.holdings or [],
                "sector_allocation": input.etf_data.sector_allocation or [],
                "country_allocation": input.etf_data.country_allocation or [],
            }

        # Check data completeness
        has_holdings = bool(etf_context.get("holdings"))
        has_sectors = bool(etf_context.get("sector_allocation"))
        has_countries = bool(etf_context.get("country_allocation"))

        data_warnings = []
        if not has_holdings:
            data_warnings.append("Holdings data unavailable - search online if needed")
        if not has_sectors:
            data_warnings.append("Sector allocation unavailable - search online if needed")
        if not has_countries:
            data_warnings.append("Country allocation unavailable - search online if needed")

        warnings_text = "\n".join([f"- {w}" for w in data_warnings]) if data_warnings else ""

        return f"""
            {base_context}

            Full ETF Data:
            {json.dumps(etf_context, indent=2)}

            {"Data Availability Notes:\n" + warnings_text if warnings_text else ""}

            Analyze holdings and allocations.
            Format as tables/lists where appropriate.
            Calculate concentration metrics (e.g., top 10 holdings percentage).

            {holdings_format}
            {sector_format}
            {incomplete_instructions}
            {source_instructions}
        """
