"""Question classification logic using AI models."""

import json
import logging
import re
import time
from typing import Dict, List, Optional

from langfuse import observe

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from core.financial_statement_type import FinancialStatementType

from .ticker_extractor import StockTickerExtractor
from .types import FinancialDataRequirement, FinancialPeriodRequirement, QuestionType

logger = logging.getLogger(__name__)


class QuestionClassifier:
    """Classifies questions to determine handling strategy."""

    # Keywords that strongly indicate quarterly report questions
    QUARTERLY_REPORT_KEYWORDS = [
        "quarterly report",
        "quarterly earnings",
        "quarterly filing",
        "earnings report",
        "10-q",
        "10q",
    ]

    # Keywords that strongly indicate annual report questions
    ANNUAL_REPORT_KEYWORDS = [
        "annual report",
        "annual filing",
        "10-k",
        "10k",
        "yearly report",
        "annual earnings",
    ]

    def __init__(self, agent: Optional[MultiAgent] = None):
        """
        Initialize the classifier.

        Args:
            agent: AI agent for classification. Creates default if not provided.
        """
        self.agent = agent or MultiAgent(model_name=ModelName.Gemini25FlashNitro)
        self.ticker_extractor = StockTickerExtractor()

    def _detect_quarterly_report_keywords(self, question: str) -> bool:
        """
        Fast keyword detection for quarterly report questions.

        Args:
            question: The question to check

        Returns:
            True if quarterly report keywords are detected
        """
        question_lower = question.lower()
        return any(keyword in question_lower for keyword in self.QUARTERLY_REPORT_KEYWORDS)

    def _detect_annual_report_keywords(self, question: str) -> bool:
        """
        Fast keyword detection for annual report questions.

        Args:
            question: The question to check

        Returns:
            True if annual report keywords are detected
        """
        question_lower = question.lower()
        return any(keyword in question_lower for keyword in self.ANNUAL_REPORT_KEYWORDS)

    @observe(name="classify_question_type")
    async def classify_question_type(
        self, question: str, ticker: str, conversation_messages: Optional[List[Dict[str, str]]] = None
    ) -> tuple[Optional[str], Optional[list[str]]]:
        """
        Classify question type with optional comparison ticker detection.

        Returns:
            Tuple of (QuestionType value or None, comparison tickers list or None)
        """
        t_start = time.perf_counter()

        # Check for comparison intent FIRST (before LLM classification)
        try:
            comparison_tickers = await self.ticker_extractor.extract_tickers(question, current_ticker=ticker)
            if len(comparison_tickers) >= 2:
                logger.info(f"Detected comparison with {len(comparison_tickers)} tickers: {comparison_tickers}")
                t_end = time.perf_counter()
                logger.info(f"Profiling classify_question_type (comparison fast path): {t_end - t_start:.4f}s")
                return QuestionType.COMPANY_COMPARISON.value, comparison_tickers
        except Exception as e:
            logger.error(f"Error in ticker extraction, continuing with normal classification: {e}")

        # Normalize ticker: treat empty/undefined as no ticker
        has_ticker = ticker and ticker.strip() and ticker.upper() not in ["UNDEFINED", "NULL", "NONE"]

        # Build conversation context if available
        conversation_context = ""
        if conversation_messages and len(conversation_messages) > 0:
            # Include last 1-2 Q/A pairs for context
            recent_messages = conversation_messages[-4:] if len(conversation_messages) >= 4 else conversation_messages
            conversation_lines = []
            for msg in recent_messages:
                role = msg.get("role", "").upper()
                content = msg.get("content", "").strip()
                if content:
                    # Truncate long content for context
                    truncated = content[:200] + "..." if len(content) > 200 else content
                    conversation_lines.append(f"{role}: {truncated}")

            if conversation_lines:
                conversation_context = "\n\nPrevious conversation context:\n" + "\n".join(conversation_lines)
                conversation_context += "\n\nIMPORTANT: If the current question is vague or ambiguous (e.g., 'Which are potential areas to reinvest?', 'What about that?', 'Tell me more'), treat it as a FOLLOW-UP to the previous conversation topic. Classify it based on the context of what was discussed before."

        ticker_context_note = ""
        if not has_ticker:
            ticker_context_note = "\n\nNOTE: No valid ticker provided (ticker is empty/undefined). Do NOT force company-specific-finance classification. If the question is about general financial concepts or strategy, classify as general-finance even if it mentions 'reinvest' or similar terms."

        prompt = f"""Classify the following question into one of these three categories.
        NOTE: The question may be in any language. Classify based on the meaning regardless of language.

        1. '{QuestionType.GENERAL_FINANCE.value}' - for general financial concepts, market trends, strategy questions, or questions about individuals that don't require specific company financial statements
        2. '{QuestionType.COMPANY_SPECIFIC_FINANCE.value}' - for questions that specifically require analyzing a company's financial statements, metrics, or performance (ONLY if a valid ticker is provided)
        3. '{QuestionType.COMPANY_GENERAL.value}' - for general questions about a company that don't require financial analysis

        Examples:
        - 'What is the average P/E ratio for the tech industry?' -> {QuestionType.GENERAL_FINANCE.value}
        - 'How does inflation affect stock markets?' -> {QuestionType.GENERAL_FINANCE.value}
        - 'How does Bill Gates' charitable giving affect his net worth?' -> {QuestionType.GENERAL_FINANCE.value}
        - 'Which are potential areas to reinvest?' (follow-up to cash flow discussion) -> {QuestionType.GENERAL_FINANCE.value}
        - 'What is Apple's revenue for the last quarter?' -> {QuestionType.COMPANY_SPECIFIC_FINANCE.value}
        - 'What was Microsoft's profit margin in 2023?' -> {QuestionType.COMPANY_SPECIFIC_FINANCE.value}
        - 'How is the company profit margin trending in recent quarters?' -> {QuestionType.COMPANY_SPECIFIC_FINANCE.value}
        - 'What are the company financial performance trends?' -> {QuestionType.COMPANY_SPECIFIC_FINANCE.value}
        - 'How is revenue growing?' -> {QuestionType.COMPANY_SPECIFIC_FINANCE.value}
        - 'What is Tesla's mission statement?' -> {QuestionType.COMPANY_GENERAL.value}
        - 'Who is the CEO of Amazon?' -> {QuestionType.COMPANY_GENERAL.value}

        Rules:
        - If the question asks about ANY financial metrics, performance, trends, or requires analyzing financial data (revenue, profit, margins, earnings, cash flow, debt, assets, growth, quarterly/annual results, etc.), AND a valid ticker is provided, classify as {QuestionType.COMPANY_SPECIFIC_FINANCE.value}
        - Financial keywords include: revenue, profit, margin, earnings, cash flow, debt, assets, liabilities, growth, performance, quarterly, annual, financial, ROE, ROI, EBITDA, operating income, net income, expenses
        - If NO valid ticker is provided (empty/undefined), do NOT classify as {QuestionType.COMPANY_SPECIFIC_FINANCE.value} even if the question mentions financial terms
        - If the question is vague/ambiguous and there's conversation context, classify based on the previous conversation topic
        - If the question is about general market trends, concepts, strategy, or individuals, classify as {QuestionType.GENERAL_FINANCE.value}
        - Only use {QuestionType.COMPANY_GENERAL.value} for non-financial company information like mission, CEO, products, history, location

        Question to classify: {question}
        Ticker context: {ticker if has_ticker else "none (empty/undefined)"}{ticker_context_note}{conversation_context}"""

        try:
            response_text = ""
            for chunk in self.agent.generate_content(prompt=prompt):
                response_text += chunk

            if QuestionType.COMPANY_SPECIFIC_FINANCE.value in response_text:
                return QuestionType.COMPANY_SPECIFIC_FINANCE.value, None
            elif QuestionType.COMPANY_GENERAL.value in response_text:
                return QuestionType.COMPANY_GENERAL.value, None
            elif QuestionType.GENERAL_FINANCE.value in response_text:
                return QuestionType.GENERAL_FINANCE.value, None
            else:
                raise ValueError(f"Unknown question type: {response_text}")

        except Exception as e:
            logger.error(f"Error classifying question type: {e}")
            return None, None
        finally:
            t_end = time.perf_counter()
            logger.info(f"Profiling classify_question_type: {t_end - t_start:.4f}s")

    @observe(name="classify_data_and_period_requirement")
    async def classify_data_and_period_requirement(
        self, ticker: str, question: str, available_metrics: Optional[list[str]] = None
    ) -> tuple[FinancialDataRequirement, Optional[FinancialPeriodRequirement], Optional[list[FinancialStatementType]]]:
        """
        Determine data level, periods, and relevant statement types in a single LLM call.

        Returns (data_requirement, period_requirement, relevant_statements).
        For DETAILED, relevant_statements is always a non-empty list of FinancialStatementType;
        when all are needed it matches FinancialStatementType.all_ordered(). For other requirements,
        relevant_statements is None.
        """
        t_start = time.perf_counter()

        # Fast path: check for quarterly report keywords before calling LLM
        if self._detect_quarterly_report_keywords(question):
            logger.info(f"Keyword pre-filter detected quarterly report question: {question[:50]}...")
            logger.info(
                f"Profiling classify_data_and_period_requirement: {time.perf_counter() - t_start:.4f}s (fast path)"
            )
            return FinancialDataRequirement.QUARTERLY_SUMMARY, None, None

        # Fast path: check for annual report keywords before calling LLM
        if self._detect_annual_report_keywords(question):
            logger.info(f"Keyword pre-filter detected annual report question: {question[:50]}...")
            logger.info(
                f"Profiling classify_data_and_period_requirement: {time.perf_counter() - t_start:.4f}s (fast path)"
            )
            return FinancialDataRequirement.ANNUAL_SUMMARY, None, None

        metrics_context = ""
        if available_metrics:
            metrics_context = f"""
            ===== Available DB Metrics =====
            Our database contains ONLY these aggregate financial metrics for {ticker.upper()}:
            {', '.join(available_metrics)}

            IMPORTANT: The database does NOT contain segment breakdowns, geographic splits, product-line revenue, revenue by source/category, or any granular sub-categories. If the question asks for data that cannot be derived from the metrics listed above (e.g., "breakdown revenue sources", "revenue by segment", "how does the company make money in detail"), return data_requirement='none'.
"""

        prompt = f"""Analyze this question about {ticker.upper()} and decide (a) what level of financial data is needed, (b) which financial periods are needed, and (c) which statement types are relevant.
            NOTE: The question may be in any language. Classify based on the meaning regardless of language.

            Question: "{question}"
{metrics_context}
            ===== Part A: data_requirement =====
            Pick exactly one of:

            1. 'none' - Question can be answered without any financial data, OR the question asks for granular data not available in our database (e.g., segment breakdowns, revenue by source, geographic splits)
            2. 'basic' - Question needs only basic company metrics like market cap, P/E ratio, basic ratios (e.g., "What is {ticker.upper()}'s market cap?", "What's the P/E ratio?", "Is {ticker.upper()} profitable?")
            3. 'detailed' - Question requires specific financial statement data like revenue, expenses, cash flow details that ARE available in our metrics (e.g., "What was {ticker.upper()}'s revenue last quarter?", "How much debt does {ticker.upper()} have?", "What's the operating margin trend?")
            4. 'quarterly_summary' - Question requires a summary of recent quarterly financial results (e.g., "Summarize the latest quarterly earnings report", "What were the key financial highlights last quarter?")
            5. 'annual_summary' - Question requires a summary of recent annual financial results (e.g., "Summarize the latest annual report", "What were the key highlights from the 10-K filing?")

            data_requirement examples:
            - "What does Apple do?" -> none
            - "Who is Tesla's CEO?" -> none
            - "What is Microsoft's market cap?" -> basic
            - "Is Amazon profitable?" -> basic
            - "What was Apple's revenue in Q3 2024?" -> detailed
            - "What's Google's debt-to-equity ratio?" -> detailed
            - "Summarize Apple's latest quarterly earnings report" -> quarterly_summary
            - "What are the key highlights from Apple's 10-K filing?" -> annual_summary

            ===== Part B: period_requirement =====
            If data_requirement is 'none' or 'basic', set period_requirement to null.
            Otherwise, fill period_requirement with:
            1. period_type: "annual", "quarterly", or "both"
            2. Specific periods: which years or quarters, or just recent periods

            period_requirement examples:
            - "What was Apple's revenue in 2023?" -> {{"period_type": "annual", "specific_years": [2023], "specific_quarters": null, "num_periods": null}}
            - "What was Apple revenue in the most recent year?" -> {{"period_type": "annual", "specific_years": null, "specific_quarters": null, "num_periods": 1}}
            - "How did Tesla perform in Q3 2024?" -> {{"period_type": "quarterly", "specific_years": null, "specific_quarters": ["2024-Q3"], "num_periods": null}}
            - "Show me Microsoft's revenue trend over the last 3 years" -> {{"period_type": "annual", "specific_years": null, "specific_quarters": null, "num_periods": 3}}
            - "Compare Amazon's Q1 and Q2 2024 results" -> {{"period_type": "quarterly", "specific_years": null, "specific_quarters": ["2024-Q1", "2024-Q2"], "num_periods": null}}
            - "What's Google's 5-year revenue growth?" -> {{"period_type": "annual", "specific_years": null, "specific_quarters": null, "num_periods": 5}}
            - "Analyze Meta's quarterly performance in 2024" -> {{"period_type": "quarterly", "specific_years": null, "specific_quarters": ["2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4"], "num_periods": null}}
            - "What were the latest quarterly results?" -> {{"period_type": "quarterly", "specific_years": null, "specific_quarters": ["latest"], "num_periods": 1}}
            - "Show both annual and quarterly trends" -> {{"period_type": "both", "specific_years": null, "specific_quarters": null, "num_periods": 3}}

            Rules for period_requirement:
            - If no specific year/quarter mentioned, use num_periods with a reasonable number (3-5)
            - Quarters should be in format "YYYY-Q#" (e.g., "2024-Q1")
            - Only fill specific_years OR specific_quarters OR num_periods, not multiple — EXCEPT when specific_quarters is ["latest"], where you must also set num_periods: 1
            - Default to annual unless quarterly is explicitly mentioned

            ===== Part C: relevant_statements =====
            If data_requirement is 'detailed', you MUST return a JSON array of which statement types are needed.
            Pick from: "income_statement", "balance_sheet", "cash_flow".
            Do NOT use null for this field when data_requirement is 'detailed'.
            If the question is broad or needs a full financial picture, return ALL three types explicitly:
            ["income_statement", "balance_sheet", "cash_flow"]

            Examples:
            - "What was revenue last quarter?" -> ["income_statement"]
            - "How much debt does the company have?" -> ["balance_sheet"]
            - "What is the free cash flow?" -> ["cash_flow"]
            - "What is the company's financial health?" -> ["income_statement", "balance_sheet", "cash_flow"]

            ===== Output =====
            Return your answer in this EXACT JSON format (no other text, no markdown fences):
            {{
                "data_requirement": "none" | "basic" | "detailed" | "quarterly_summary" | "annual_summary",
                "period_requirement": null | {{
                    "period_type": "annual" | "quarterly" | "both",
                    "specific_years": [2023, 2024] | null,
                    "specific_quarters": ["2024-Q1", "2024-Q2"] | ["latest"] | null,
                    "num_periods": 1 | null
                }},
                "relevant_statements": ["income_statement"] | ["balance_sheet", "cash_flow"] | ["income_statement", "balance_sheet", "cash_flow"] | null
            }}
            Note: When data_requirement is not 'detailed', set relevant_statements to null.
        """

        try:
            response_text = ""
            for chunk in self.agent.generate_content(prompt=prompt):
                response_text += chunk

            parsed = self._parse_json_from_response(response_text)

            data_req_str = str(parsed.get("data_requirement", "")).lower().strip()
            if "detailed" in data_req_str:
                data_requirement = FinancialDataRequirement.DETAILED
            elif "quarterly_summary" in data_req_str:
                data_requirement = FinancialDataRequirement.QUARTERLY_SUMMARY
            elif "annual_summary" in data_req_str:
                data_requirement = FinancialDataRequirement.ANNUAL_SUMMARY
            elif "basic" in data_req_str:
                data_requirement = FinancialDataRequirement.BASIC
            elif "none" in data_req_str:
                data_requirement = FinancialDataRequirement.NONE
            else:
                logger.warning(f"Unknown data_requirement value '{data_req_str}', defaulting to BASIC")
                data_requirement = FinancialDataRequirement.BASIC

            period_requirement: Optional[FinancialPeriodRequirement] = None
            if data_requirement in (
                FinancialDataRequirement.DETAILED,
                FinancialDataRequirement.QUARTERLY_SUMMARY,
                FinancialDataRequirement.ANNUAL_SUMMARY,
            ):
                period_block = parsed.get("period_requirement")
                if isinstance(period_block, dict):
                    try:
                        period_requirement = FinancialPeriodRequirement(
                            period_type=period_block.get("period_type", "annual"),
                            specific_years=period_block.get("specific_years"),
                            specific_quarters=period_block.get("specific_quarters"),
                            num_periods=period_block.get("num_periods"),
                        )
                    except Exception as inner:
                        logger.warning(
                            f"Failed to build FinancialPeriodRequirement from {period_block}: {inner}; using fallback"
                        )
                        period_requirement = self._fallback_period(data_requirement)
                else:
                    logger.warning(
                        f"Missing/invalid period_requirement for data_requirement={data_requirement}; using fallback"
                    )
                    period_requirement = self._fallback_period(data_requirement)

            # Parse relevant_statements (DETAILED only — always explicit list, never null)
            relevant_statements: Optional[list[FinancialStatementType]] = None
            if data_requirement == FinancialDataRequirement.DETAILED:
                raw_stmts = parsed.get("relevant_statements")
                all_members = set(FinancialStatementType)
                if isinstance(raw_stmts, list) and raw_stmts:
                    deduped: list[FinancialStatementType] = []
                    for raw in raw_stmts:
                        if not isinstance(raw, str):
                            continue
                        try:
                            st = FinancialStatementType(raw)
                        except ValueError:
                            continue
                        if st not in deduped:
                            deduped.append(st)
                    if not deduped:
                        relevant_statements = list(FinancialStatementType.all_ordered())
                    elif set(deduped) == all_members:
                        relevant_statements = list(FinancialStatementType.all_ordered())
                    else:
                        relevant_statements = deduped
                else:
                    relevant_statements = list(FinancialStatementType.all_ordered())
                logger.info(f"Relevant statements: {[s.value for s in relevant_statements]}")

            return data_requirement, period_requirement, relevant_statements

        except Exception as e:
            logger.error(f"Error classifying data + period requirement: {e}")
            return FinancialDataRequirement.BASIC, None, None
        finally:
            t_end = time.perf_counter()
            logger.info(f"Profiling classify_data_and_period_requirement: {t_end - t_start:.4f}s")

    @staticmethod
    def _fallback_period(data_requirement: FinancialDataRequirement) -> FinancialPeriodRequirement:
        """Build a sensible fallback period when the LLM response is unparseable."""
        if data_requirement == FinancialDataRequirement.QUARTERLY_SUMMARY:
            return FinancialPeriodRequirement(period_type="quarterly", num_periods=1)
        if data_requirement == FinancialDataRequirement.ANNUAL_SUMMARY:
            return FinancialPeriodRequirement(period_type="annual", num_periods=1)
        return FinancialPeriodRequirement(period_type="annual", num_periods=3)

    def _parse_json_from_response(self, response_text: str) -> dict:
        """Parse JSON from response, handling markdown code blocks."""
        # Try markdown code block first
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Try raw JSON
            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
            else:
                raise ValueError(f"No JSON found in response: {response_text}")

        return json.loads(json_str)
