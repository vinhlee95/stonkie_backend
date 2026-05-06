"""Context builder for DETAILED financial data requirement."""

import logging

from .base import ContextBuilder, ContextBuilderInput
from .components import PromptComponents

logger = logging.getLogger(__name__)


class DetailedContextBuilder(ContextBuilder):
    """Builds context for questions requiring detailed financial analysis."""

    def build(self, input: ContextBuilderInput) -> str:
        """Build context for detailed financial analysis."""
        if input.deep_analysis:
            return self._build_deep_analysis(input)
        return self._build_short_analysis(input)

    def _build_short_analysis(self, input: ContextBuilderInput) -> str:
        """Build context for short, scannable analysis (default)."""
        base_context = PromptComponents.base_context(input.ticker, input.question)
        section_structure = PromptComponents.section_structure_template()
        available_sources = PromptComponents.available_sources(
            input.ticker, input.annual_statements, input.quarterly_statements
        )
        data_coverage = PromptComponents.data_coverage_notice(input.annual_statements, input.quarterly_statements)

        return f"""
            {base_context}

            {PromptComponents.grounding_rules()}

            Company Fundamental Data:
            {input.company_fundamental}

            Annual Financial Statements:
            {input.annual_statements}

            Quarterly Financial Statements:
            {input.quarterly_statements}

            {data_coverage}

            {available_sources}

            **Instructions for your analysis:**

            Analyze the financial data and provide a clear, direct answer to the user's question.
            Choose the response format that best serves the answer — a direct response for simple questions, or organized sections for complex multi-topic analysis.
            Use AT MOST 2-3 sections. Start each section heading on its own line in markdown bold: **Section Title**

            {section_structure}

            Answer in a professional, informative tone. Prioritize clarity and directness.
        """

    def _build_deep_analysis(self, input: ContextBuilderInput) -> str:
        """Build context for comprehensive, detailed analysis."""
        base_context = PromptComponents.base_context(input.ticker, input.question)

        available_sources = PromptComponents.available_sources(
            input.ticker, input.annual_statements, input.quarterly_statements
        )
        data_coverage = PromptComponents.data_coverage_notice(input.annual_statements, input.quarterly_statements)

        return f"""
            {base_context}

            {PromptComponents.grounding_rules()}

            Company Fundamental Data:
            {input.company_fundamental}

            Annual Financial Statements:
            {input.annual_statements}

            Quarterly Financial Statements:
            {input.quarterly_statements}

            {data_coverage}

            {available_sources}

            **Instructions for your analysis:**

            Structure your response with EXACTLY 3 sections in this order:

            1. (~80 words) A concise overview that previews the key findings from the two sections below. Highlight the most important takeaway.

            2. (~160 words) Choose a section title (max 6 words) for the most relevant aspect of the question. Identify 2-3 key focus points and analyze them in depth.

            3. (~160 words) Choose a section title (max 6 words) for a second important aspect. Identify 2-3 key focus points and analyze them in depth.

            The two section titles should be specific to the question asked (e.g., "Revenue Growth Trajectory" not generic "Financial Performance").

            **Formatting Guidelines:**
            - Start each section with its title in markdown bold: **Section Title**
            - Add a blank line after the title before starting the paragraph
            - Each section should be a cohesive paragraph (or 2-3 short paragraphs)
            - Use numbers strategically - select 2-4 key figures per section that best support your analysis
            - Use the largest appropriate unit for numbers (e.g., "$1.5 billion" not "$1,500 million", "5.2%" not "520 basis points")
            - Keep total response under 400 words

            **Analysis Rules:**
            - PRIORITIZE REASONING: Explain WHY trends occur, WHAT drives the changes, and WHAT it means for the business
            - STRATEGIC USE OF NUMBERS: Include specific figures only when they strengthen your argument or illustrate a key point
            - IDENTIFY DRIVERS: Explain the underlying business factors, market conditions, or strategic decisions behind the numbers
            - CONNECT THE DOTS: Link financial performance to business strategy, competitive position, and market dynamics
            - NO DUPLICATION: Each sentence should add new information
        """
