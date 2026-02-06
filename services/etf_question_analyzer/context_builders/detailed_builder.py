"""Context builder for detailed ETF analysis questions."""

import json
import logging
from dataclasses import asdict
from typing import Dict, List, Optional

from .base import ETFContextBuilder, ETFContextBuilderInput
from .components import ETFPromptComponents

logger = logging.getLogger(__name__)


class DetailedETFBuilder(ETFContextBuilder):
    """Builds context for detailed ETF analysis using full data."""

    def build(self, input: ETFContextBuilderInput) -> str:
        """Build context for detailed ETF analysis questions."""
        if input.deep_analysis:
            return self._build_deep_analysis(input)
        return self._build_short_analysis(input)

    def _build_short_analysis(self, input: ETFContextBuilderInput) -> str:
        """Build context for short, dynamic analysis (default)."""
        base_context = ETFPromptComponents.base_context(input.ticker, input.question)
        section_structure = ETFPromptComponents.section_structure_template()
        example_structure = ETFPromptComponents.example_structure()
        etf_formatting = ETFPromptComponents.etf_data_formatting()
        source_instructions = ETFPromptComponents.source_instructions()

        # Serialize ETF data
        etf_context = self._serialize_etf_data(input.etf_data)

        # Check data completeness
        warnings_text = self._check_data_completeness(etf_context)

        return f"""
            {base_context}

            Full ETF Data:
            {json.dumps(etf_context, indent=2)}

            {"Data Availability Notes:\n" + warnings_text if warnings_text else ""}

            **Instructions for your analysis:**

            Analyze the ETF data and organize your findings into multiple focused sections.
            You decide how many sections are needed to thoroughly cover the key aspects that answer the user's question.
            Aim for around 3 sections, but use fewer if the question is narrow or more if it requires broader coverage.

            {section_structure}

            {example_structure}

            {etf_formatting}

            {source_instructions}

            Answer in a professional, informative tone. Prioritize clarity and scannability over narrative flow.
        """

    def _build_deep_analysis(self, input: ETFContextBuilderInput) -> str:
        """Build context for comprehensive, detailed analysis with structured sections."""
        base_context = ETFPromptComponents.base_context(input.ticker, input.question)
        etf_formatting = ETFPromptComponents.etf_data_formatting()
        source_instructions = ETFPromptComponents.source_instructions()

        # Serialize ETF data
        etf_context = self._serialize_etf_data(input.etf_data)

        # Use AI-generated sections or fallback to default structure
        sections = input.dimension_sections
        if not sections or not self._validate_sections(sections):
            logger.info("Using fallback section structure for ETF deep analysis")
            sections = [
                {
                    "title": "Holdings & Allocation Analysis",
                    "focus_points": [
                        "Analyze top holdings concentration and diversification",
                        "Sector and geographic allocation breakdown",
                    ],
                },
                {
                    "title": "Cost Efficiency & Performance",
                    "focus_points": [
                        "TER comparison and cost efficiency",
                        "Fund size and tracking accuracy",
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

        # Check data completeness
        warnings_text = self._check_data_completeness(etf_context)

        return f"""
            {base_context}

            Full ETF Data:
            {json.dumps(etf_context, indent=2)}

            {"Data Availability Notes:\n" + warnings_text if warnings_text else ""}

            **Instructions for your analysis:**

            Structure your response with EXACTLY 3 sections in this order:

            (~{summary_words} words) Provide a concise overview that previews the key findings from the two sections below. Highlight the most important takeaway.

            {sections_text}

            **Formatting Guidelines:**
            - Start each section with its title in markdown bold: **Section Title**
            - Add a blank line after the title before starting the paragraph
            - Each section should be a cohesive paragraph (or 2-3 short paragraphs)
            - Use numbers strategically - select 2-4 key figures per section that best support your analysis
            - Use the largest appropriate unit for numbers (e.g., "$1.5B" not "$1,500M", "0.07%" not "7 basis points")
            - Keep total response under 300 words

            **Analysis Rules:**
            - PRIORITIZE REASONING: Explain WHY certain allocations exist, WHAT drives the ETF's strategy, and WHAT it means for investors
            - STRATEGIC USE OF NUMBERS: Include specific figures only when they strengthen your argument or illustrate a key point
            - IDENTIFY DRIVERS: Explain the underlying investment strategy, market conditions, or structural decisions
            - CONNECT THE DOTS: Link ETF characteristics to investor goals, market positioning, and risk-return profile
            - NO DUPLICATION: Each sentence should add new information
            - USE SEARCH WISELY: Get up-to-date context for market trends and competitive landscape

            {etf_formatting}

            {source_instructions}
        """

    def _serialize_etf_data(self, etf_data) -> Dict:
        """Convert ETFFundamentalDto to dictionary for JSON serialization."""
        if not etf_data:
            return {}

        return {
            "name": etf_data.name,
            "ticker": etf_data.ticker,
            "ter_percent": etf_data.ter_percent,
            "fund_size_billions": etf_data.fund_size_billions,
            "index_tracked": etf_data.index_tracked,
            "holdings": [asdict(h) for h in etf_data.holdings] if etf_data.holdings else [],
            "sector_allocation": [asdict(s) for s in etf_data.sector_allocation] if etf_data.sector_allocation else [],
            "country_allocation": [asdict(c) for c in etf_data.country_allocation]
            if etf_data.country_allocation
            else [],
        }

    def _check_data_completeness(self, etf_context: Dict) -> str:
        """Check for missing data and generate warnings."""
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

        return "\n".join([f"- {w}" for w in data_warnings]) if data_warnings else ""

    def _validate_sections(self, sections: Optional[List[Dict]]) -> bool:
        """Validate dimension section structure."""
        if not sections or len(sections) != 2:
            return False

        for section in sections:
            if "title" not in section or "focus_points" not in section:
                return False
            # Validate title length (max 6 words)
            title_words = section["title"].split()
            if len(title_words) > 6:
                return False

        return True
