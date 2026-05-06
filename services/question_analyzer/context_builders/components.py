"""Shared prompt components for context builders."""

import logging
import re
from datetime import date
from typing import Dict, List

from core.financial_statement_type import FinancialStatementType
from services.shared.prompt_utils import visual_output_instructions

logger = logging.getLogger(__name__)


class PromptComponents:
    """Reusable prompt fragments for financial context building."""

    @staticmethod
    def current_date() -> str:
        """Return current date context for prompt grounding."""
        today = date.today()
        formatted = today.strftime("%B %d, %Y")
        current_year = today.year
        prior_year = current_year - 1
        current_quarter_num = (today.month - 1) // 3 + 1
        if current_quarter_num == 1:
            last_completed_quarter = f"{prior_year}-Q4"
        else:
            last_completed_quarter = f"{current_year}-Q{current_quarter_num - 1}"
        return (
            f"Today is {formatted}; the most recently completed reporting quarter is {last_completed_quarter}. "
            f'Treat "latest"/"recent"/"this quarter" as {last_completed_quarter} (or newer) and "this year"/"YTD" as {current_year}.'
        )

    @staticmethod
    def grounding_rules() -> str:
        """Unified grounding rules. Inject once near the top of any v2 prompt that supplies data blocks."""
        return (
            "**Grounding rules (read first):**\n"
            "- The data blocks below — Sources, Company Fundamental Data, Annual/Quarterly Financial Statements — are the authoritative dataset for this answer. Prefer them over your training knowledge whenever they conflict; the provided data wins.\n"
            '- Do not introduce facts (numbers, dates, names, events) not present in the data blocks. If the question (or part of it) cannot be answered from the data, say so explicitly (e.g. "this metric is not available in the data I have access to") and do not backfill from memory.\n'
            "- Treat the data as current even if it post-dates your training cutoff; do not refuse on cutoff grounds.\n"
            '- Do not write source attributions inline in the answer in ANY form. Forbidden: [SOURCES_JSON] blocks, bracketed markers like [1] or [2], parentheticals like "(Source: …)" / "(per Reuters)", and lead-ins like "according to …" or "as reported by …". The UI renders sources in a separate footer; your prose must contain only the analysis itself.'
        )

    @staticmethod
    def no_data_decline() -> str:
        """Used when no sources AND no DB data are available — instruct the model to decline rather than freelance from training."""
        return (
            "**No current data available.** No web sources or financial data were retrieved for this question. "
            "Do not answer from training knowledge. Reply briefly that you do not have current data to answer reliably, "
            "and suggest the user retry or rephrase."
        )

    @staticmethod
    def base_context(ticker: str, question: str) -> str:
        """Build the base context that's common to all prompts."""
        date_context = PromptComponents.current_date()
        return f"""
            You are a seasoned financial analyst. {date_context}
            Your task is to provide an insightful, non-repetitive analysis for the following question.
            IMPORTANT: You MUST respond in the same language as the CURRENT question below, regardless of the language used in previous conversation history.

            Question: {question}
            Company: {ticker.upper()}
        """

    @staticmethod
    def source_instructions() -> str:
        """Build detailed source citation instructions."""
        return """
            **Source Citation Rules (follow strictly):**
            0. Freshness rule: unless the user explicitly asks for historical periods, avoid citing years older than current year - 1.
            1. NEVER write source names, document titles, or citation references as regular text inside your paragraphs.
               ALL citations must appear exclusively inside [SOURCES_JSON] blocks — not at the end of a sentence, not in parentheses.
            2. After EACH paragraph, emit a sources block on its own line for the sources used in THAT paragraph:
               [SOURCES_JSON]{"sources": [{"name": "Source Name", "url": "https://full-url"}]}[/SOURCES_JSON]
            3. Each paragraph MUST be followed by its own [SOURCES_JSON] block. Do NOT batch all sources at the end.
            4. For SEC filings: use the EXACT URLs from the "Available Source URLs" section above.
            5. For web sources: use the FULL URL with path from search results. NEVER use bare domains like "macrumors.com".
            6. For financial statement data with no URL available: in the JSON only, include the `name` field and omit `url`.
               Do not repeat that name in the paragraph text.
            7. NEVER invent or guess URLs. Only cite URLs explicitly provided in context or search results.
            8. Only include sources that were actually used to generate that specific paragraph.

            Example output (correct):
            Apple's revenue grew 6.4% to $416B in FY2024.
            [SOURCES_JSON]{"sources": [{"name": "SEC 10-K Filing 2024", "url": "https://www.sec.gov/Archives/..."}]}[/SOURCES_JSON]

            Services revenue hit a record $109B, up 13% year-over-year.
            [SOURCES_JSON]{"sources": [{"name": "Apple Q4 2025 Press Release", "url": "https://www.apple.com/newsroom/..."}]}[/SOURCES_JSON]

            Do NOT do this (wrong — redundant inline source name before the block):
            Revenue grew 6.4% in FY2024. SEC 10-K Filing 2024
            [SOURCES_JSON]{"sources": [{"name": "SEC 10-K Filing 2024", "url": "https://..."}]}[/SOURCES_JSON]
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
        """Build the adaptive response format template."""
        return """
            **Response Format — match length to the question's complexity:**
            - For single-fact questions (e.g. "what is X", "how much did Y earn"): answer in 1-2 sentences. State the fact and stop. Do NOT add extra context, related metrics, or explanation unless asked.
            - For simple comparison or trend questions: answer in 1-3 short paragraphs. No section headings. State the facts clearly and add a brief "why" if relevant.
            - For multi-faceted analytical questions covering 3+ distinct topics: use bold section headings (**Heading**) to organize, with a brief intro paragraph first. Limit to 1-2 sections.
            - In ALL cases: be concise, make every word count, avoid filler phrases.
            - Bold important figures and percentages.
            - Use the largest appropriate unit for numbers (e.g., "$1.5 billion" not "$1,500 million").
            - If using section headings: keep them specific and catchy (3-5 words max), on separate bolded lines with a blank line after each heading.
            - Keep each section to 2-3 sentences MAX (under 60 words). Tell the story behind the numbers, not every detail.
        """

    @staticmethod
    def example_structure() -> str:
        """Build the example structure template showing both formats."""
        return """
            **Example A — Direct answer (for simple/comparison questions):**

            Apple's revenue grew 6.4% to **$416B** in FY2024, while Samsung reported **$211B** in the same period. Apple's growth was driven primarily by Services, which hit a record **$109B** (+13% YoY). Samsung's semiconductor recovery pushed operating profit up **$21B**, though mobile revenue remained flat.

            **Example B — Sectioned answer (for complex multi-topic analysis):**

            [Brief intro answering the question]

            **Section Heading 1**

            [Concise finding with key metrics]

            **Section Heading 2**

            [Another focused insight]
        """

    @staticmethod
    def visual_output_instructions() -> str:
        """Instructions for emitting inline SVG/HTML visuals via fenced code blocks."""
        return visual_output_instructions()

    @staticmethod
    def data_grounding_rules() -> str:
        return """
            CRITICAL DATA GROUNDING RULES:
            - You may ONLY cite numbers and facts that are explicitly present in the financial data provided above. Calculations derived solely from provided figures (e.g., margins, growth rates, ratios) are allowed.
            - If the requested metric (e.g., dividend per share, buyback amount, insider ownership) does not appear in the provided data and cannot be derived from it, you MUST explicitly state: "This specific metric is not available in the financial data I have access to."
            - Do NOT present figures that would require data not present in the statements above.
            - Do NOT cite any source URL unless it was explicitly provided in the context above or returned by Google Search results.
            - If you cannot fully answer the question with the provided data, suggest the user check the company's investor relations page for this specific information.
        """

    @staticmethod
    def data_coverage_notice(annual_statements: List[Dict], quarterly_statements: List[Dict]) -> str:
        metric_names: set = set()
        for stmt in annual_statements + quarterly_statements:
            for key in FinancialStatementType:
                section = stmt.get(key)
                if isinstance(section, dict):
                    metric_names.update(section.keys())
        if not metric_names:
            return ""
        sorted_metrics = ", ".join(sorted(metric_names))
        return (
            f"**Available Financial Metrics in Provided Data:**\n"
            f"The following metrics are available: {sorted_metrics}\n\n"
            f"IMPORTANT: If the user's question asks about a metric NOT in the above list, "
            f"you MUST state that this specific data is not available. "
            f"Do NOT fabricate or estimate values for metrics not listed above."
        )

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
