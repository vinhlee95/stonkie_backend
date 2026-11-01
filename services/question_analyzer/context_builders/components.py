"""Shared prompt components for context builders."""

import logging
import re
from datetime import date
from typing import Dict, List, Optional

from core.financial_statement_type import FinancialStatementType
from core.quarter_period import parse_quarter_end
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
    def narrative_answer_format() -> str:
        """Answer shape for stock v2 answers: the story behind the numbers, not a restatement of them."""
        return (
            "**How to write the answer — tell the story behind the numbers:**\n"
            "1. Lead with the takeaway in plain words: what is happening and why. No list of numbers in the first sentence.\n"
            "2. Then the drivers: the business and real-world causes (product cycles, demand shifts, pricing, costs, "
            "deals, one-off gains or charges, regulation, macro). Tie each driver to what the Sources report. Use only "
            "the few numbers that prove the point.\n"
            "3. End with what it means: the implication for the business or investors, or what to watch next.\n"
            "- A single-fact question gets the fact first, then a compact context paragraph (driver, relevant event, "
            "implication). An analytical question gets a few short paragraphs, or 2-3 bold section headings when it "
            "spans several topics. No fixed sentence limit, but keep it well short of a page.\n"
            "- Events, names and dates are allowed only when they appear in the Sources (including web_search results). "
            "Never add events from training knowledge.\n"
            "- If a notable move is not explained by the sources, say so plainly. You may give a clearly labelled "
            'inference from the numbers (e.g. "likely a non-operating gain such as an investment mark-up"), never an '
            "invented event.\n"
            "- Do not list every period's value, do not restate a chart in prose, and do not open with remarks like "
            '"X is not a reported metric": interpret the user\'s wording (e.g. "comp profit margin" means the '
            "company's profit margins) and answer.\n"
            '- Bold the key figures. Use the largest sensible unit ("$1.5 billion", not "$1,500 million").'
        )

    @staticmethod
    def search_instruction() -> str:
        """Tells the answer model when to call its web_search tool (stock v2 answers with tools)."""
        return (
            "**Before writing:** check every notable move in the data (large jumps or drops in a line item, margin "
            "step changes, one-off gains or charges). If the Sources do not explain a move, call `web_search` with "
            "a specific query (company name, period, line item) to find the cause. Call tools before writing any "
            "text and do not announce that you are searching. If the Sources already explain the picture, answer "
            "directly."
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
    def no_sources_search_first() -> str:
        """No sources yet but the answer model has web_search: search first, decline only if that finds nothing too."""
        return (
            "**No web sources yet.** The initial search found nothing relevant. Call `web_search` for what the "
            "question needs. If it also finds nothing, reply briefly that you do not have current data to answer "
            "reliably and suggest the user retry or rephrase; do not answer from training knowledge."
        )

    @staticmethod
    def answer_date_context(quarterly_statements: Optional[List[Dict]] = None) -> str:
        """Date context for answer prompts: today's date plus the newest quarter actually in the data.

        Unlike current_date(), this never asserts a calendar quarter as "latest": fiscal quarters
        and filing lag mean the newest reported quarter is usually older, and the model would
        relabel the data with it.
        """
        today = date.today()
        context = f"Today is {today.strftime('%B %d, %Y')}."
        quarter_ends = [parse_quarter_end(stmt.get("period_end_quarter")) for stmt in quarterly_statements or []]
        quarter_ends = [d for d in quarter_ends if d]
        if quarter_ends:
            latest = max(quarter_ends)
            context += (
                f" Latest quarter in the financial statements below: the quarter ended {latest.strftime('%B %d, %Y')}."
            )
        return (
            f"{context} Label each period by the period-end date or fiscal year shown in the data; never relabel it "
            f'as a different quarter or year. "This year"/"YTD" means {today.year}.'
        )

    @staticmethod
    def base_context(ticker: str, question: str, quarterly_statements: Optional[List[Dict]] = None) -> str:
        """Build the base context that's common to all prompts."""
        date_context = PromptComponents.answer_date_context(quarterly_statements)
        return f"""
            You are a seasoned financial analyst. {date_context}
            Your task is to provide an insightful, non-repetitive analysis for the following question.
            IMPORTANT: You MUST respond in the same language as the CURRENT question below, regardless of the language used in previous conversation history.

            Question: {question}
            Company: {ticker.upper()}
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
