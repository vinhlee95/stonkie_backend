"""Question classification logic using AI models."""

import json
import logging
import re
import time
from typing import Optional

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName

from .types import FinancialDataRequirement, FinancialPeriodRequirement, QuestionType

logger = logging.getLogger(__name__)


class QuestionClassifier:
    """Classifies questions to determine handling strategy."""

    def __init__(self, agent: Optional[MultiAgent] = None):
        """
        Initialize the classifier.

        Args:
            agent: AI agent for classification. Creates default if not provided.
        """
        self.agent = agent or MultiAgent(model_name=ModelName.Gemini25FlashLite)

    async def classify_question_type(self, question: str) -> Optional[str]:
        """
        Classify question as general finance, company general, or company-specific finance.

        Args:
            question: The question to classify

        Returns:
            QuestionType value or None if classification fails
        """
        t_start = time.perf_counter()

        prompt = f"""Classify the following question into one of these three categories:
        1. '{QuestionType.GENERAL_FINANCE.value}' - for general financial concepts, market trends, or questions about individuals that don't require specific company financial statements
        2. '{QuestionType.COMPANY_SPECIFIC_FINANCE.value}' - for questions that specifically require analyzing a company's financial statements
        3. '{QuestionType.COMPANY_GENERAL.value}' - for general questions about a company that don't require financial analysis

        Examples:
        - 'What is the average P/E ratio for the tech industry?' -> {QuestionType.GENERAL_FINANCE.value}
        - 'How does inflation affect stock markets?' -> {QuestionType.GENERAL_FINANCE.value}
        - 'How does Bill Gates' charitable giving affect his net worth?' -> {QuestionType.GENERAL_FINANCE.value}
        - 'What is Apple's revenue for the last quarter?' -> {QuestionType.COMPANY_SPECIFIC_FINANCE.value}
        - 'What was Microsoft's profit margin in 2023?' -> {QuestionType.COMPANY_SPECIFIC_FINANCE.value}
        - 'What is Tesla's mission statement?' -> {QuestionType.COMPANY_GENERAL.value}
        - 'Who is the CEO of Amazon?' -> {QuestionType.COMPANY_GENERAL.value}

        Rules:
        - If the question requires analyzing specific company financial statements or metrics, classify as {QuestionType.COMPANY_SPECIFIC_FINANCE.value}
        - If the question is about general market trends, concepts, or individuals, classify as {QuestionType.GENERAL_FINANCE.value}
        - If the question is about company information but doesn't need financial analysis, classify as {QuestionType.COMPANY_GENERAL.value}

        Question to classify: {question}"""

        try:
            response_text = ""
            for chunk in self.agent.generate_content(prompt=prompt):
                response_text += chunk

            if QuestionType.COMPANY_SPECIFIC_FINANCE.value in response_text:
                return QuestionType.COMPANY_SPECIFIC_FINANCE.value
            elif QuestionType.COMPANY_GENERAL.value in response_text:
                return QuestionType.COMPANY_GENERAL.value
            elif QuestionType.GENERAL_FINANCE.value in response_text:
                return QuestionType.GENERAL_FINANCE.value
            else:
                raise ValueError(f"Unknown question type: {response_text}")

        except Exception as e:
            logger.error(f"Error classifying question type: {e}")
            return None
        finally:
            t_end = time.perf_counter()
            logger.info(f"Profiling classify_question_type: {t_end - t_start:.4f}s")

    async def classify_data_requirement(self, ticker: str, question: str) -> FinancialDataRequirement:
        """
        Determine what level of financial data is needed.

        Args:
            ticker: Company ticker symbol
            question: The question being asked

        Returns:
            FinancialDataRequirement level
        """
        t_start = time.perf_counter()

        prompt = f"""Analyze this question about {ticker.upper()} and determine what level of financial data is needed:
            Question: "{question}"

            Classify into one of these categories:

            1. 'none' - Question can be answered without any financial data (e.g., "What does {ticker.upper()} do?", "Who is the CEO?", "What industry is {ticker.upper()} in?")

            2. 'basic' - Question needs only basic company metrics like market cap, P/E ratio, basic ratios (e.g., "What is {ticker.upper()}'s market cap?", "What's the P/E ratio?", "Is {ticker.upper()} profitable?")

            3. 'detailed' - Question requires specific financial statement data like revenue, expenses, cash flow details (e.g., "What was {ticker.upper()}'s revenue last quarter?", "How much debt does {ticker.upper()} have?", "What's the operating margin trend?")

            Examples:
            - "What does Apple do?" -> none
            - "Who is Tesla's CEO?" -> none  
            - "What is Microsoft's market cap?" -> basic
            - "Is Amazon profitable?" -> basic
            - "What was Apple's revenue in Q3 2024?" -> detailed
            - "How much cash does Tesla have?" -> detailed
            - "What's Google's debt-to-equity ratio?" -> detailed

            Return only the classification: none, basic, or detailed
        """

        try:
            response_text = ""
            for chunk in self.agent.generate_content(prompt=prompt):
                response_text += chunk

            response_text = response_text.lower().strip()

            if "detailed" in response_text:
                return FinancialDataRequirement.DETAILED
            elif "basic" in response_text:
                return FinancialDataRequirement.BASIC
            else:
                return FinancialDataRequirement.NONE

        except Exception as e:
            logger.error(f"Error classifying data requirement: {e}")
            return FinancialDataRequirement.BASIC
        finally:
            t_end = time.perf_counter()
            logger.info(f"Profiling classify_data_requirement: {t_end - t_start:.4f}s")

    async def classify_period_requirement(self, ticker: str, question: str) -> FinancialPeriodRequirement:
        """
        Determine which specific financial periods are needed.

        Args:
            ticker: Company ticker symbol
            question: The question being asked

        Returns:
            FinancialPeriodRequirement specification
        """
        t_start = time.perf_counter()

        prompt = f"""Analyze this question about {ticker.upper()} and determine which financial periods are needed:
            Question: "{question}"

            Determine:
            1. Period type needed: "annual", "quarterly", or "both"
            2. Specific periods: Which years or quarters? Or just recent periods?

            Examples:
            - "What was Apple's revenue in 2023?" -> annual, years: [2023]
            - "What was Apple revenue in the most recent year?" -> annual, num_periods: 1
            - "How did Tesla perform in Q3 2024?" -> quarterly, quarters: ["2024-Q3"]
            - "Show me Microsoft's revenue trend over the last 3 years" -> annual, num_periods: 3
            - "Compare Amazon's Q1 and Q2 2024 results" -> quarterly, quarters: ["2024-Q1", "2024-Q2"]
            - "What's Google's 5-year revenue growth?" -> annual, num_periods: 5
            - "Analyze Meta's quarterly performance in 2024" -> quarterly, quarters: ["2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4"]
            - "What was Netflix's annual revenue last year?" -> annual, num_periods: 1
            - "Show both annual and quarterly trends" -> both, num_periods: 3

            Return your answer in this EXACT JSON format (no other text):
            {{
                "period_type": "annual" | "quarterly" | "both",
                "specific_years": [2023, 2024] or null,
                "specific_quarters": ["2024-Q1", "2024-Q2"] or null,
                "num_periods": 3 or null
            }}

            Rules:
            - If no specific year/quarter mentioned, use num_periods with a reasonable number (3-5)
            - Quarters should be in format "YYYY-Q#" (e.g., "2024-Q1")
            - Only fill specific_years OR specific_quarters OR num_periods, not multiple
            - Default to annual unless quarterly is explicitly mentioned
        """

        try:
            response_text = ""
            for chunk in self.agent.generate_content(prompt=prompt):
                response_text += chunk

            parsed = self._parse_json_from_response(response_text)

            return FinancialPeriodRequirement(
                period_type=parsed.get("period_type", "annual"),
                specific_years=parsed.get("specific_years"),
                specific_quarters=parsed.get("specific_quarters"),
                num_periods=parsed.get("num_periods"),
            )

        except Exception as e:
            logger.error(f"Error classifying period requirement: {e}")
            return FinancialPeriodRequirement(period_type="annual", num_periods=3)
        finally:
            t_end = time.perf_counter()
            logger.info(f"Profiling classify_period_requirement: {t_end - t_start:.4f}s")

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
