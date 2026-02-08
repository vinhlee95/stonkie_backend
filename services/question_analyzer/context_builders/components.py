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
            **Source Citation Rules (follow strictly):**
            1. After EACH paragraph, emit a sources block on its own line for the sources used in THAT paragraph:
               [SOURCES_JSON]{"sources": [{"name": "Source Name", "url": "https://full-url"}]}[/SOURCES_JSON]
            2. Each paragraph MUST be followed by its own [SOURCES_JSON] block. Do NOT batch all sources at the end.
            3. For SEC filings: use the EXACT URLs from the "Available Source URLs" section above.
            4. For web sources: use the FULL URL with path from search results. NEVER use bare domains like "macrumors.com".
            5. For financial statement data with no URL available: include only the name, omit the url field.
            6. NEVER invent or guess URLs. Only cite URLs explicitly provided in context or search results.
            7. Only include sources that were actually used to generate that specific paragraph.

            Example output:
            Apple's revenue grew 6.4% to $416B in FY2024.
            [SOURCES_JSON]{"sources": [{"name": "SEC 10-K Filing 2024", "url": "https://www.sec.gov/Archives/..."}]}[/SOURCES_JSON]

            Services revenue hit a record $109B, up 13% year-over-year.
            [SOURCES_JSON]{"sources": [{"name": "Apple Q4 2025 Press Release", "url": "https://www.apple.com/newsroom/..."}]}[/SOURCES_JSON]
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

    @staticmethod
    def available_sources(
        ticker: str,
        annual_statements: List[Dict],
        quarterly_statements: List[Dict],
    ) -> str:
        """Extract filing URLs from statement data into a reference block for the prompt."""
        lines = []
        for stmt in annual_statements:
            url = stmt.get("filing_10k_url")
            year = stmt.get("period_end_year", "unknown")
            if url:
                lines.append(f"- {ticker.upper()} Annual 10-K Filing ({year}): {url}")
        for stmt in quarterly_statements:
            url = stmt.get("filing_10q_url")
            quarter = stmt.get("period_end_quarter", "unknown")
            if url:
                lines.append(f"- {ticker.upper()} Quarterly 10-Q Filing ({quarter}): {url}")
        if not lines:
            return ""
        header = "**Available Source URLs (use these EXACT URLs when citing):**"
        return f"{header}\n" + "\n".join(lines)

    @staticmethod
    def build_filing_url_lookup(
        ticker: str,
        annual_statements: List[Dict],
        quarterly_statements: List[Dict],
    ) -> Dict[str, str]:
        """Build a name→URL lookup from statement filing URLs for source enrichment."""
        lookup: Dict[str, str] = {}
        for stmt in annual_statements:
            url = stmt.get("filing_10k_url")
            year = stmt.get("period_end_year", "unknown")
            if url:
                # Match the exact name format used in available_sources()
                lookup[f"{ticker.upper()} Annual 10-K Filing ({year})"] = url
                # Also add common AI-generated variants
                lookup[f"SEC 10-K Filing {year}"] = url
                lookup[f"SEC 10-K Filing ({year})"] = url
                lookup[f"10-K Filing {year}"] = url
                lookup[f"Annual Report {year}"] = url
        for stmt in quarterly_statements:
            url = stmt.get("filing_10q_url")
            quarter = stmt.get("period_end_quarter", "unknown")
            if url:
                lookup[f"{ticker.upper()} Quarterly 10-Q Filing ({quarter})"] = url
                lookup[f"SEC 10-Q Filing {quarter}"] = url
                lookup[f"SEC 10-Q Filing ({quarter})"] = url
                lookup[f"10-Q Filing {quarter}"] = url
                lookup[f"Quarterly Statement {quarter}"] = url
        return lookup

    @staticmethod
    def extract_sources_from_response(text: str) -> List[Dict]:
        """Extract sources from [Sources: ...] blocks in response text.

        Returns deduplicated list of {"name": str, "url": str|None}.
        """
        sources: List[Dict] = []
        seen_keys: set = set()
        # Match all [Sources: ...] blocks
        for block_match in re.finditer(r"\[Sources?:\s*(.+?)\]", text):
            block_content = block_match.group(1)
            # Extract markdown links [Name](url)
            for link_match in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", block_content):
                name, url = link_match.group(1), link_match.group(2)
                key = url
                if key not in seen_keys:
                    seen_keys.add(key)
                    sources.append({"name": name, "url": url})
            # Extract plain text sources (not inside markdown links)
            # Remove markdown links first, then split by comma
            plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", "", block_content)
            for part in plain.split(","):
                part = part.strip()
                if part:
                    key = part
                    if key not in seen_keys:
                        seen_keys.add(key)
                        sources.append({"name": part, "url": None})
        return sources
