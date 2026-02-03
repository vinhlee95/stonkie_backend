"""Context builder for ETF comparison questions."""

from dataclasses import dataclass

from connectors.etf_fundamental import ETFFundamentalDto

from .components import ETFPromptComponents


@dataclass
class ComparisonContextBuilderInput:
    """Input data for building ETF comparison context."""

    tickers: list[str]
    question: str
    etf_data_list: list[ETFFundamentalDto]
    use_google_search: bool


class ComparisonETFBuilder:
    """Builds context for multi-ETF comparison questions."""

    def build(self, input: ComparisonContextBuilderInput) -> str:
        """
        Build context for ETF comparison questions.

        Args:
            input: The input data containing multiple ETFs

        Returns:
            Formatted prompt string with structured multi-ETF data
        """
        # Build numbered ETF summaries
        etf_summaries = []
        for i, etf in enumerate(input.etf_data_list, 1):
            summary = self._build_etf_summary(i, etf)
            etf_summaries.append(summary)

        etf_summaries_text = "\n\n".join(etf_summaries)

        # Comparison instructions
        comparison_instructions = """
## Instructions

Create a comprehensive side-by-side comparison of these ETFs.

**Comparison Aspects:**
- **Cost Analysis:** Compare TER, fund size, and cost efficiency
- **Holdings Analysis:** Compare top holdings, concentration risk, overlap
- **Sector/Country Allocation:** Compare diversification strategies
- **All Other Relevant Factors:** Based on the specific question

**Formatting Guidelines:**
- Use markdown tables for side-by-side comparison
- Highlight key differences and similarities
- Provide clear recommendations if asked
- Calculate metrics (e.g., top 10 holdings concentration, sector overlap)

**Example Table Format:**
| ETF | Ticker | TER | Fund Size | Top Holding |
|-----|--------|-----|-----------|-------------|
| Name 1 | XXX | 0.07% | $50B | Company A (7%) |
| Name 2 | YYY | 0.09% | $30B | Company B (6%) |
"""

        source_instructions = ETFPromptComponents.source_instructions()

        return f"""
# ETF Comparison Analysis

## ETFs to Compare

{etf_summaries_text}

## User Question

"{input.question}"

{comparison_instructions}

{source_instructions}
        """

    def _build_etf_summary(self, number: int, etf: ETFFundamentalDto) -> str:
        """
        Build a structured summary for a single ETF.

        Args:
            number: The ETF number (1, 2, 3, etc.)
            etf: The ETF data

        Returns:
            Formatted ETF summary string
        """
        # Core metadata
        summary_lines = [f"### {number}. **{etf.name}**"]

        if etf.ticker:
            summary_lines.append(f"- **Ticker:** {etf.ticker}")
        if etf.isin:
            summary_lines.append(f"- **ISIN:** {etf.isin}")

        # Key metrics
        metrics = []
        if etf.ter_percent is not None:
            metrics.append(f"TER: {etf.ter_percent}%")
        if etf.fund_size_billions is not None:
            metrics.append(f"Fund Size: ${etf.fund_size_billions:.2f}B")
        if etf.holdings:
            metrics.append(f"Holdings: {len(etf.holdings)}")

        if metrics:
            summary_lines.append(f"- **Key Metrics:** {' | '.join(metrics)}")

        # Provider and index
        if etf.fund_provider:
            summary_lines.append(f"- **Provider:** {etf.fund_provider}")
        if etf.index_tracked:
            summary_lines.append(f"- **Index:** {etf.index_tracked}")

        # Top holdings (limit to 5 for comparison)
        if etf.holdings:
            top_holdings = etf.holdings[:5]
            holdings_str = ", ".join([f"{h.name} ({h.weight_percent}%)" for h in top_holdings])
            summary_lines.append(f"- **Top Holdings:** {holdings_str}")
        else:
            summary_lines.append("- **Top Holdings:** Data unavailable")

        # Top sectors (limit to 3)
        if etf.sector_allocation:
            top_sectors = etf.sector_allocation[:3]
            sectors_str = ", ".join([f"{s.sector} ({s.weight_percent}%)" for s in top_sectors])
            summary_lines.append(f"- **Top Sectors:** {sectors_str}")
        else:
            summary_lines.append("- **Top Sectors:** Data unavailable")

        # Top countries (limit to 3)
        if etf.country_allocation:
            top_countries = etf.country_allocation[:3]
            countries_str = ", ".join([f"{c.country} ({c.weight_percent}%)" for c in top_countries])
            summary_lines.append(f"- **Geographic Focus:** {countries_str}")

        return "\n".join(summary_lines)
