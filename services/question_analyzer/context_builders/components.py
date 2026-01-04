"""Shared prompt components for context builders."""

import logging
import re
from typing import Dict, List

logger = logging.getLogger(__name__)


def validate_section_titles(sections: List[Dict]) -> bool:
    """
    Validate section structure and titles.

    Args:
        sections: List of section dictionaries

    Returns:
        True if valid, False otherwise
    """
    if not sections or not isinstance(sections, list):
        logger.error("Sections is not a valid list")
        return False

    if len(sections) != 2:
        logger.error(f"Invalid number of sections: {len(sections)} (must be exactly 2)")
        return False

    for i, section in enumerate(sections):
        # Check required keys
        if "title" not in section or "focus_points" not in section:
            logger.error(f"Section {i} missing required keys: {section}")
            return False

        title = section["title"]
        focus_points = section["focus_points"]

        # Validate title
        if not isinstance(title, str) or not title.strip():
            logger.error(f"Section {i} has invalid title: {title}")
            return False

        # Check word count (max 6 words)
        word_count = len(title.split())
        if word_count > 6:
            logger.error(f"Section {i} title too long ({word_count} words): {title}")
            return False

        # Check for special characters (allow letters, numbers, spaces, &, -)
        if re.search(r"[^a-zA-Z0-9\s&-]", title):
            logger.error(f"Section {i} title contains special characters: {title}")
            return False

        # Validate focus points
        if not isinstance(focus_points, list) or len(focus_points) == 0:
            logger.error(f"Section {i} has invalid focus_points: {focus_points}")
            return False

        for j, point in enumerate(focus_points):
            if not isinstance(point, str) or not point.strip():
                logger.error(f"Section {i} focus_point {j} is invalid: {point}")
                return False

    return True


class PromptComponents:
    """Reusable prompt fragments for financial context building."""

    @staticmethod
    def base_context(ticker: str, question: str) -> str:
        """Build the base context that's common to all prompts."""
        return f"""
            You are a seasoned financial analyst. Your task is to provide an insightful, non-repetitive analysis for the following question.

            Question: {question}
            Company: {ticker.upper()}
        """

    @staticmethod
    def source_instructions() -> str:
        """Build detailed source citation instructions."""
        return """
            Make sure to specify the source and source link of the answer at the end of the analysis. The format should be:
            Sources: [Source Name](Source Link), [Source Name](Source Link)

            If the source is official 10K or 10Q filings, mention SEC in the source, e.g. SEC Filing for 2025 Q1 and provide the link.

            If the source is from the financial statements provided in the context, no need to link, but mention clearly which statement it is from and which year or quarter it pertains to.
            MAKE SURE TO FOLLOW THIS FORMAT: 
            Sources: Annual Report 2023, Quarterly Statement Q1 2024

            Only include sources that were actually used to generate the analysis.
        """

    @staticmethod
    def analysis_focus() -> str:
        """Build analytical reasoning focus prompt."""
        return """
            Focus on analytical reasoning and interpretation. Use select key numbers to support your analysis,
            but prioritize explaining WHY trends exist and WHAT drives the financial performance.
            Include a few specific figures where they strengthen your argument, but avoid listing exhaustive metrics.
        """

    @staticmethod
    def formatting_guidelines() -> str:
        """Build standard formatting guidelines."""
        return """
            **Formatting Guidelines:**
            - Start each section with its title in markdown bold: **Section Title**
            - Add a blank line after the title before starting the paragraph
            - Use numbers strategically - select 2-4 key figures per section that best support your analysis
            - Use the largest appropriate unit for numbers (e.g., "$1.5 billion" not "$1,500 million")
        """

    @staticmethod
    def section_structure_template() -> str:
        """Build the standard section structure template."""
        return """
            **Structure:**
            - Start with a brief introductory paragraph (under 50 words) that directly answers the user's question
            - Follow with multiple focused sections, each covering a distinct key aspect or finding
            - Each section should have a bold, descriptive heading: **Section Heading**
            - Keep each section content under 30 words - be concise and to the point

            **Section Guidelines:**
            - Each section heading should be specific, descriptive, and catchy (3-5 words max). The section headings must be in separate lines and bolded.
            - Add a new line after each section heading.
            - Each section content should focus on ONE key finding or aspect
            - Include specific numbers and metrics where relevant
            - Highlight significant changes, trends, or anomalies
            - Bold important figures and percentages
            - Prioritize the most relevant information that directly answers the user's question
            - Use the largest appropriate unit for numbers (e.g., "$1.5 billion" not "$1,500 million")
            - Make every word count - avoid filler phrases
        """

    @staticmethod
    def example_structure() -> str:
        """Build the example structure template."""
        return """
            **Example Structure:**

            [Brief intro answering the question - under 50 words]

            **Section Heading 1**

            [Concise finding with key metrics - under 30 words]

            **Section Heading 2**

            [Another focused insight - under 30 words]

            [Continue with additional sections as needed...]
        """
