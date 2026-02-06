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
    short_analysis: bool = False


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
        import logging

        logger = logging.getLogger(__name__)
        logger.info(f"ComparisonBuilder - Building prompt with short_analysis={input.short_analysis}")

        if input.short_analysis:
            return self._build_short_analysis(input)
        else:
            return self._build_deep_analysis(input)

    def _build_short_analysis(self, input: ComparisonContextBuilderInput) -> str:
        """Build short analysis comparison prompt with structured sections."""
        etf_summaries_text = self._build_etf_summaries(input, short_analysis=True)
        section_structure = ETFPromptComponents.comparison_section_structure()

        comparison_instructions = f"""
            ## Instructions

            Analyze the ETF comparison and organize your findings into multiple focused sections.

            **ETF Comparison Table Format:**

            Start with a vertical table showing key metrics side-by-side:

            | Metric       | [ETF1 Full Name] | [ETF2 Full Name] | [ETF3 Full Name] | [ETF4 Full Name] |
            |--------------|------------------|------------------|------------------|------------------|
            | Provider     | ...              | ...              | ...              | ...              |
            | TER          | X.XX%            | X.XX%            | ...              | ...              |
            | Fund Size    | $XXB             | $XXB             | ...              | ...              |
            | Top Holding  | Name (X.XX%)     | Name (X.XX%)     | ...              | ...              |
            | Top Sector   | Name (X.XX%)     | Name (X.XX%)     | ...              | ...              |
            | Top Country  | Name (X.XX%)     | Name (X.XX%)     | ...              | ...              |

            {section_structure}

            **Suggested Section Topics for ETF Comparisons (choose what's most relevant):**
            - **Cost Efficiency** - TER comparison and cost implications
            - **Fund Size & Liquidity** - Size differences and trading implications
            - **Holdings Overlap** - Top holdings comparison and concentration
            - **Geographic Diversification** - Country allocation differences
            - **Sector Concentration** - Industry exposure differences
            - **Investment Suitability** - Which ETF for which investor profile

            **Example ETF Comparison Structure:**

            | Metric | SXR8 | SPYY |
            |--------|------|------|
            | Provider | iShares | SPDR ETF |
            | TER | 0.07% | 0.12% |
            | Fund Size | $114.62B | $7.63B |
            [...table continues...]

            [Brief overview paragraph answering the question - under 50 words]

            **Cost Efficiency**

            SXR8 charges **0.07% TER** vs SPYY's **0.12%**, saving $50 annually per $100k invested.

            **Geographic Diversification**

            SXR8 focuses on US (**95.83%**), while SPYY offers global exposure (**61.89% US**, rest international).

            **Investment Suitability**

            SXR8 suits US-focused investors seeking low costs; SPYY better for global diversification seekers.

            {ETFPromptComponents.source_instructions()}

            Answer in a professional, informative tone. Prioritize clarity and scannability over narrative flow.
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

    def _build_deep_analysis(self, input: ComparisonContextBuilderInput) -> str:
        """Build deep analysis comparison prompt with comprehensive details."""
        etf_summaries_text = self._build_etf_summaries(input, short_analysis=False)

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

    def _build_etf_summaries(self, input: ComparisonContextBuilderInput, short_analysis: bool) -> str:
        """Build numbered ETF summaries."""
        etf_summaries = []
        for i, etf in enumerate(input.etf_data_list, 1):
            summary = self._build_etf_summary(i, etf, short_analysis)
            etf_summaries.append(summary)
        return "\n\n".join(etf_summaries)

    def _build_etf_summary(self, number: int, etf: ETFFundamentalDto, short_analysis: bool = False) -> str:
        """
        Build a structured summary for a single ETF with data reduction based on mode.

        Args:
            number: The ETF number (1, 2, 3, etc.)
            etf: The ETF data
            short_analysis: Whether to use short mode with reduced data

        Returns:
            Formatted ETF summary string
        """
        # Core metadata
        summary_lines = [f"### {number}. **{etf.name}**"]

        # Always show ticker
        if etf.ticker:
            summary_lines.append(f"- **Ticker:** {etf.ticker}")

        # Skip ISIN in short mode
        if not short_analysis and etf.isin:
            summary_lines.append(f"- **ISIN:** {etf.isin}")

        # Key metrics
        metrics = []
        if etf.ter_percent is not None:
            metrics.append(f"TER: {etf.ter_percent}%")
        if etf.fund_size_billions is not None:
            metrics.append(f"Fund Size: ${etf.fund_size_billions:.2f}B")
        if not short_analysis and etf.holdings:
            metrics.append(f"Holdings: {len(etf.holdings)}")

        if metrics:
            summary_lines.append(f"- **Key Metrics:** {' | '.join(metrics)}")

        # Always show provider
        if etf.fund_provider:
            summary_lines.append(f"- **Provider:** {etf.fund_provider}")

        # Skip index in short mode
        if not short_analysis and etf.index_tracked:
            summary_lines.append(f"- **Index:** {etf.index_tracked}")

        # Holdings: 2 for short, 5 for comprehensive
        if etf.holdings:
            holdings_limit = 2 if short_analysis else 5
            top_holdings = etf.holdings[:holdings_limit]
            holdings_str = ", ".join([f"{h.name} ({h.weight_percent}%)" for h in top_holdings])
            summary_lines.append(f"- **Top Holdings:** {holdings_str}")
        else:
            summary_lines.append("- **Top Holdings:** Data unavailable")

        # Sectors: 1 for short, 3 for comprehensive
        if etf.sector_allocation:
            sectors_limit = 1 if short_analysis else 3
            top_sectors = etf.sector_allocation[:sectors_limit]
            sectors_str = ", ".join([f"{s.sector} ({s.weight_percent}%)" for s in top_sectors])
            summary_lines.append(f"- **Diversification:** {sectors_str}")
        else:
            summary_lines.append("- **Diversification:** Data unavailable")

        # Countries: 1 for short, 3 for comprehensive
        if etf.country_allocation:
            countries_limit = 1 if short_analysis else 3
            top_countries = etf.country_allocation[:countries_limit]
            countries_str = ", ".join([f"{c.country} ({c.weight_percent}%)" for c in top_countries])
            summary_lines.append(f"- **Geographic Focus:** {countries_str}")

        return "\n".join(summary_lines)
