"""Context builder for DETAILED financial data requirement."""

import logging

from .base import ContextBuilder, ContextBuilderInput
from .components import PromptComponents, validate_section_titles

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
        example_structure = PromptComponents.example_structure()

        return f"""
            {base_context}
            
            Company Fundamental Data:
            {input.company_fundamental}

            Annual Financial Statements:
            {input.annual_statements}
            
            Quarterly Financial Statements:
            {input.quarterly_statements}

            **Instructions for your analysis:**

            Analyze the financial data and organize your findings into multiple focused sections.
            You decide how many sections are needed to thoroughly cover the key aspects that answer the user's question. Try to keep the number of sections as small as possible.

            {section_structure}

            {example_structure}

            {PromptComponents.source_instructions()}

            Answer in a professional, informative tone. Prioritize clarity and scannability over narrative flow.
        """

    def _build_deep_analysis(self, input: ContextBuilderInput) -> str:
        """Build context for comprehensive, detailed analysis."""
        base_context = PromptComponents.base_context(input.ticker, input.question)

        # Use AI-generated sections or fallback to default structure
        sections = input.dimension_sections
        if not sections or not validate_section_titles(sections):
            logger.info("Using fallback section structure")
            sections = [
                {
                    "title": "Financial Performance",
                    "focus_points": [
                        "Analyze key metrics from the statements (revenue, net income, profit margins)",
                        "Explain year-over-year growth/decline trends and patterns",
                    ],
                },
                {
                    "title": "Strategic Positioning",
                    "focus_points": [
                        "Industry context and competitive position",
                        "Future outlook, opportunities, and growth risks",
                    ],
                },
            ]

        # Word allocation: 80 words for summary, 160 words each for 2 main sections (total: 400)
        summary_words = 80
        section_words = 160

        # Build dynamic section instructions for the 2 main sections
        sections_text = ""
        for section in sections:
            sections_text += f"\n**{section['title']}**\n\n"
            sections_text += f"(~{section_words} words) Focus on:\n"
            for point in section["focus_points"]:
                sections_text += f"- {point}\n"
            sections_text += "\n"

        return f"""
            {base_context}
            
            Company Fundamental Data:
            {input.company_fundamental}

            Annual Financial Statements:
            {input.annual_statements}
            
            Quarterly Financial Statements:
            {input.quarterly_statements}
            
            **Instructions for your analysis:**

            Structure your response with EXACTLY 3 sections in this order:
            
            (~{summary_words} words) Provide a concise overview that previews the key findings from the two sections below. Highlight the most important takeaway.

            {sections_text}

            **Formatting Guidelines:**
            - Start each section with its title in markdown bold: **Section Title**
            - Add a blank line after the title before starting the paragraph
            - Each section should be a cohesive paragraph (or 2-3 short paragraphs)
            - Use numbers strategically - select 2-4 key figures per section that best support your analysis
            - Use the largest appropriate unit for numbers (e.g., "$1.5 billion" not "$1,500 million", "5.2%" not "520 basis points")
            - Keep total response under 300 words
            
            **Analysis Rules:**
            - PRIORITIZE REASONING: Explain WHY trends occur, WHAT drives the changes, and WHAT it means for the business
            - STRATEGIC USE OF NUMBERS: Include specific figures only when they strengthen your argument or illustrate a key point
            - IDENTIFY DRIVERS: Explain the underlying business factors, market conditions, or strategic decisions behind the numbers
            - CONNECT THE DOTS: Link financial performance to business strategy, competitive position, and market dynamics
            - NO DUPLICATION: Each sentence should add new information
            - USE SEARCH WISELY: Get up-to-date context for industry trends and competitive landscape
            
            {PromptComponents.source_instructions()}
        """
