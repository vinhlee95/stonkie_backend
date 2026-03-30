"""Shared prompt components for ETF context builders."""

from datetime import date


class ETFPromptComponents:
    """Reusable prompt fragments for ETF analysis."""

    @staticmethod
    def current_date() -> str:
        """Return current date context for prompt grounding."""
        today = date.today()
        formatted = today.strftime("%B %d, %Y")
        current_year = today.year
        prior_year = current_year - 1
        return (
            f"Today's date is {formatted}. Current year is {current_year}. "
            f"Unless the user explicitly asks for historical periods, only use data from {current_year} or {prior_year}. "
            "If fresh numeric ETF data cannot be verified, provide a qualitative best-effort answer and state the limitation."
        )

    @staticmethod
    def base_context(ticker: str, question: str) -> str:
        """Build the base context for ETF analysis."""
        date_context = ETFPromptComponents.current_date()
        ticker_display = ticker.upper() if ticker and ticker.upper() not in ["UNDEFINED", "NULL", "NONE"] else "the ETF"
        return f"""
            You are an ETF analyst specializing in exchange-traded funds. {date_context}

            Question: {question}
            ETF: {ticker_display}
        """

    @staticmethod
    def source_instructions() -> str:
        """Build source citation requirements."""
        return """
            **CRITICAL - Source Citation:**
            - Freshness rule: unless user explicitly asks historical data, avoid citing years older than current year - 1
            - ALWAYS cite sources at the end: Sources: [Name](URL)
            - For database ETF data: "Sources: ETF Fundamental Data"
            - For Google Search results: cite specific URLs
            - NEVER fabricate information - if data missing, state clearly and search online
        """

    @staticmethod
    def etf_data_formatting() -> str:
        """Guidelines for formatting ETF data."""
        return """
            **Formatting ETF Data:**
            - TER: Show as percentage with 2 decimals (0.07%)
            - Fund size: Use billions with 1 decimal ($114.6B)
            - Dates: Use readable format (Jan 2010)
            - Percentages: 2 decimals for weights (7.38%)
        """

    @staticmethod
    def holdings_formatting() -> str:
        """Guidelines for formatting holdings."""
        return """
            **Holdings Format:**
            - Numbered list: 1. Company Name - XX.XX%
            - Calculate top 10 concentration (sum of top 10 weights)
            - Highlight concentration risk if >40%
            - Note any single holding >5%
        """

    @staticmethod
    def sector_formatting() -> str:
        """Guidelines for sector/country allocation."""
        return """
            **Allocation Format:**
            - List sectors/countries by weight (highest first)
            - Show percentage for each
            - Identify dominant sector if >30%
            - Note diversification level
        """

    @staticmethod
    def incomplete_data_instructions() -> str:
        """Handle missing ETF data."""
        return """
            **Missing Data Handling:**
            - If holdings/sectors missing: state clearly "Holdings data unavailable"
            - Enable Google Search to find information
            - MUST cite sources when using Google Search
            - Be transparent about data limitations
        """

    @staticmethod
    def comparison_section_structure() -> str:
        """Build section structure template specifically for ETF comparisons."""
        return """
            **Structure:**
            - Follow up with 1-2 focused comparison sections, each covering a distinct aspect
            - Each section should have a bold, descriptive heading: **Section Heading**, followed by a blank line before the content
            - Keep each section to 2-3 sentences MAX (under 60 words) - concise but tell the story behind the numbers

            **Section Guidelines:**
            - Each section heading should be specific and descriptive (3-6 words). Section headings must be in separate lines and bolded.
            - Add a new line after each section heading.
            - Each section should compare a specific aspect across the ETFs
            - Include concrete numbers, percentages, and metrics for comparison
            - Highlight key differences and similarities
            - Bold important figures and percentages for quick scanning
            - Use the largest appropriate unit (e.g., "$1.5B" not "$1,500M")
            - Make every word count - avoid filler phrases
        """

    @staticmethod
    def section_structure_template() -> str:
        """Build the adaptive response format template for dynamic analysis."""
        return """
            **Response Format — match length to the question's complexity:**
            - For single-fact questions (e.g. "what is X", "how much did Y earn"): answer in 1-2 sentences. State the fact and stop. Do NOT add extra context, related metrics, or explanation unless asked.
            - For simple comparison or trend questions: answer in 1-3 short paragraphs. No section headings. State the facts clearly and add a brief "why" if relevant.
            - For multi-faceted analytical questions covering 3+ distinct topics: use bold section headings (**Heading**) to organize, with a brief intro paragraph first. Limit to 1-2 sections.
            - In ALL cases: be concise, make every word count, avoid filler phrases.
            - Bold important figures and percentages.
            - Use the largest appropriate unit for numbers (e.g., "$1.5B" not "$1,500M").
            - If using section headings: keep them specific and catchy (3-5 words max), on separate bolded lines with a blank line after each heading.
            - Keep each section to 2-3 sentences MAX (under 60 words). Tell the story behind the numbers, not every detail.
        """

    @staticmethod
    def example_structure() -> str:
        """Build the example structure template showing both formats."""
        return """
            **Example A — Direct answer (for simple/comparison questions):**

            VOO returned **28.4%** in 2024 vs QQQ's **26.8%**, narrowing the typical gap. VOO's broader diversification across **500 stocks** provided steadier gains, while QQQ's tech concentration added volatility. Both significantly outperformed the bond market's **2.1%** return.

            **Example B — Sectioned answer (for complex multi-topic analysis):**

            [Brief intro answering the question]

            **Section Heading 1**

            [Concise finding with key metrics]

            **Section Heading 2**

            [Another focused insight]
        """
