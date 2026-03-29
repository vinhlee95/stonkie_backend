"""Context builder for stock company comparison questions."""

from dataclasses import dataclass, field
from typing import Any

from connectors.company import CompanyFundamentalDto

from .components import PromptComponents


@dataclass
class CompanyComparisonData:
    """Data for a single company in a comparison."""

    ticker: str
    fundamental: CompanyFundamentalDto | None = None
    quarterly_statements: list[dict[str, Any]] = field(default_factory=list)
    data_source: str = "database"  # "database" or "training_data"


@dataclass
class ComparisonCompanyBuilderInput:
    """Input data for building stock company comparison context."""

    tickers: list[str]
    question: str
    companies_data: list[CompanyComparisonData]
    use_google_search: bool
    short_analysis: bool = False


def _format_number(value: int | float, prefix: str = "$") -> str:
    """Format large numbers with B/M/K suffixes."""
    if value == 0:
        return "N/A"
    abs_val = abs(value)
    if abs_val >= 1_000_000_000_000:
        return f"{prefix}{value / 1_000_000_000_000:.2f}T"
    if abs_val >= 1_000_000_000:
        return f"{prefix}{value / 1_000_000_000:.2f}B"
    if abs_val >= 1_000_000:
        return f"{prefix}{value / 1_000_000:.2f}M"
    if abs_val >= 1_000:
        return f"{prefix}{value / 1_000:.2f}K"
    return f"{prefix}{value}"


class ComparisonCompanyBuilder:
    """Builds context for multi-company stock comparison questions."""

    def build(self, input: ComparisonCompanyBuilderInput) -> str:
        if input.short_analysis:
            return self._build_short_analysis(input)
        else:
            return self._build_deep_analysis(input)

    def _build_short_analysis(self, input: ComparisonCompanyBuilderInput) -> str:
        company_summaries = self._build_company_summaries(input, short_analysis=True)
        training_data_warning = self._build_training_data_warning(input)

        return f"""
            # Stock Comparison Analysis

            **LANGUAGE RULE: Detect the language of the User Question below. Your ENTIRE response (including table headers, section headings, and all text) MUST be written in that same language. If the question is in English, respond in English. If Vietnamese, respond in Vietnamese. This overrides all other instructions.**

            ## Companies to Compare

            {company_summaries}

            ## User Question

            "{input.question}"

            ## Instructions

            Analyze the stock comparison and organize your findings into focused sections.

            Start with a 1-2 sentence summary that directly answers the user's question with a clear verdict or key takeaway. Do NOT write generic filler like "below is a comparison" — state the actual insight.

            Then show a vertical comparison table with the most relevant metrics for the question. Choose which metrics to include based on what the user is asking about.

            Then write 2-3 focused sections analyzing the most relevant comparison aspects (valuation, profitability, growth, dividends, industry position).

            Each section must have a **bold markdown heading** (e.g. **Profitability**) on its own line. Use specific numbers. Don't just state facts — explain WHY the differences exist (e.g. business model, revenue mix, market dynamics, competitive position, strategic decisions). Connect the numbers to the underlying drivers.

            {training_data_warning}

            {PromptComponents.source_instructions()}

            Answer in a professional, informative tone. Prioritize clarity and scannability.
            REMINDER: Your response language MUST match the User Question language.
        """

    def _build_deep_analysis(self, input: ComparisonCompanyBuilderInput) -> str:
        company_summaries = self._build_company_summaries(input, short_analysis=False)
        training_data_warning = self._build_training_data_warning(input)

        return f"""
            # Stock Comparison Analysis

            **LANGUAGE RULE: Detect the language of the User Question below. Your ENTIRE response (including table headers, section headings, and all text) MUST be written in that same language. If the question is in English, respond in English. If Vietnamese, respond in Vietnamese. This overrides all other instructions.**

            ## Companies to Compare

            {company_summaries}

            ## User Question

            "{input.question}"

            ## Instructions

            Create a comprehensive side-by-side comparison of these companies.

            Cover: valuation, financial performance, quarterly trends, dividends & returns, industry position.

            Use markdown tables for side-by-side comparison. Highlight key differences. Include quarterly trend data where available.

            {training_data_warning}

            Do NOT include [SOURCES_JSON] blocks. The data is from our database and has no URLs to cite.

            REMINDER: Your response language MUST match the User Question language.
        """

    def _build_training_data_warning(self, input: ComparisonCompanyBuilderInput) -> str:
        training_data_tickers = [c.ticker for c in input.companies_data if c.data_source == "training_data"]
        if not training_data_tickers:
            return ""
        tickers_str = ", ".join(training_data_tickers)
        return f"""
            **IMPORTANT — Data Source Warning:**
            The following tickers have NO financial data in our database: {tickers_str}.
            For these tickers, you are using your training data. You MUST explicitly state in your response
            that the information for {tickers_str} comes from training data and may not be current or accurate.
            Clearly distinguish between data-backed analysis and training-data-based analysis.
        """

    def _build_company_summaries(self, input: ComparisonCompanyBuilderInput, short_analysis: bool) -> str:
        summaries = []
        for i, company_data in enumerate(input.companies_data, 1):
            summary = self._build_company_summary(i, company_data, short_analysis)
            summaries.append(summary)
        return "\n\n".join(summaries)

    def _build_company_summary(self, number: int, data: CompanyComparisonData, short_analysis: bool) -> str:
        lines = []

        if data.data_source == "training_data":
            lines.append(f"### {number}. **{data.ticker}** [TRAINING_DATA]")
            lines.append("- **Data Source:** Training data (no database records)")
            return "\n".join(lines)

        f = data.fundamental
        if not f:
            lines.append(f"### {number}. **{data.ticker}**")
            lines.append("- **Data:** Fundamental data unavailable")
            return "\n".join(lines)

        lines.append(f"### {number}. **{f.name}** ({data.ticker})")
        lines.append(f"- **Sector:** {f.sector} | **Industry:** {f.industry}")
        lines.append(
            f"- **Key Metrics:** Market Cap: {_format_number(f.market_cap)} | "
            f"P/E: {f.pe_ratio:.2f} | EPS: ${f.basic_eps:.2f}"
        )
        lines.append(
            f"- **Revenue (TTM):** {_format_number(f.revenue)} | " f"**Net Income:** {_format_number(f.net_income)}"
        )
        lines.append(f"- **Dividend Yield:** {f.dividend_yield:.2f}%")

        if not short_analysis and data.quarterly_statements:
            lines.append("- **Recent Quarters:**")
            for stmt in data.quarterly_statements[:3]:
                period = stmt.get("period_end_quarter", "N/A")
                income = stmt.get("income_statement", {})
                revenue = income.get("total_revenue") or income.get("revenue", "N/A")
                net_inc = income.get("net_income", "N/A")
                lines.append(f"  - {period}: Revenue={revenue}, Net Income={net_inc}")

        return "\n".join(lines)
