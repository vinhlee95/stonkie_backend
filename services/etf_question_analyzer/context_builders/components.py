"""Shared prompt components for ETF context builders."""


class ETFPromptComponents:
    """Reusable prompt fragments for ETF analysis."""

    @staticmethod
    def base_context(ticker: str, question: str) -> str:
        """Build the base context for ETF analysis."""
        ticker_display = ticker.upper() if ticker and ticker.upper() not in ["UNDEFINED", "NULL", "NONE"] else "the ETF"
        return f"""
            You are an ETF analyst specializing in exchange-traded funds.

            Question: {question}
            ETF: {ticker_display}
        """

    @staticmethod
    def source_instructions() -> str:
        """Build source citation requirements."""
        return """
            **CRITICAL - Source Citation:**
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
