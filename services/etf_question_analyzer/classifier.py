"""ETF question classification logic using AI models."""

import logging
import time
from typing import Optional

from langfuse import observe

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName

from .ticker_extractor import ETFTickerExtractor
from .types import ETFDataRequirement, ETFQuestionType

logger = logging.getLogger(__name__)


class ETFQuestionClassifier:
    """Classifies ETF questions to determine handling strategy."""

    def __init__(self, agent: Optional[MultiAgent] = None):
        """
        Initialize the classifier.

        Args:
            agent: AI agent for classification. Creates default if not provided.
        """
        self.agent = agent or MultiAgent(model_name=ModelName.Gemini30Flash)
        self.ticker_extractor = ETFTickerExtractor()

    @observe(name="etf_classify_question")
    async def classify_question(
        self, ticker: str, question: str
    ) -> tuple[ETFQuestionType, ETFDataRequirement, Optional[list[str]]]:
        """
        Classify ETF question and determine data requirements.

        Args:
            ticker: ETF ticker symbol (may be empty/undefined for general questions)
            question: The question to classify

        Returns:
            Tuple of (question_type, data_requirement, comparison_tickers)
            - comparison_tickers is None for single-ETF questions, list[str] for comparisons
        """
        t_start = time.perf_counter()

        # Check for comparison intent FIRST (before LLM classification)
        comparison_tickers = await self.ticker_extractor.extract_tickers(question)
        if len(comparison_tickers) >= 2:
            logger.info(f"Detected comparison question with {len(comparison_tickers)} tickers: {comparison_tickers}")
            return ETFQuestionType.ETF_COMPARISON, ETFDataRequirement.DETAILED, comparison_tickers

        # Normalize ticker for single-ETF classification
        has_ticker = ticker and ticker.strip() and ticker.upper() not in ["UNDEFINED", "NULL", "NONE"]

        prompt = f"""Classify this ETF question into a category and data requirement level.

Question: "{question}"
ETF Ticker: {ticker if has_ticker else "none (general question)"}

Categories:
1. 'general_etf' - General ETF education questions (no specific ETF)
   Examples: "What is an ETF?", "Difference between physical and synthetic replication?", "What is TER?"

2. 'etf_overview' - Basic ETF information questions
   Examples: "What is the TER?", "Who provides this ETF?", "What index does it track?"

3. 'etf_detailed_analysis' - Questions requiring holdings/sector/country data
   Examples: "What are the top holdings?", "Show sector allocation", "Geographic breakdown?"

Data Requirements:
- 'none' - Can answer without ETF data (general education)
- 'basic' - Needs only core metadata (name, TER, provider, index tracked)
- 'detailed' - Requires full data (holdings, sectors, countries)

Rules:
- If NO ticker provided or general ETF question -> general_etf + none
- If asks about TER, provider, index, launch date -> etf_overview + basic
- If asks about holdings, sectors, countries, allocation -> etf_detailed_analysis + detailed

Return ONLY in this JSON format:
{{
  "question_type": "general_etf|etf_overview|etf_detailed_analysis",
  "data_requirement": "none|basic|detailed",
  "reasoning": "brief explanation"
}}"""

        try:
            response_text = ""
            for chunk in self.agent.generate_content(prompt=prompt):
                response_text += chunk

            # Parse JSON response
            import json

            # Try to extract JSON from response
            response_text = response_text.strip()
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            result = json.loads(response_text)

            question_type = ETFQuestionType(result["question_type"])
            data_requirement = ETFDataRequirement(result["data_requirement"])

            logger.info(
                f"ETF classification: {question_type.value}, data: {data_requirement.value}, reasoning: {result.get('reasoning', 'N/A')}"
            )

            return question_type, data_requirement, None

        except Exception as e:
            logger.error(
                f"Error classifying ETF question: {e}, response: {response_text[:200] if response_text else 'N/A'}"
            )
            # Fallback: if no ticker, assume general question
            if not has_ticker:
                return ETFQuestionType.GENERAL_ETF, ETFDataRequirement.NONE, None
            # Otherwise assume basic overview question
            return ETFQuestionType.ETF_OVERVIEW, ETFDataRequirement.BASIC, None

        finally:
            t_end = time.perf_counter()
            logger.info(f"ETF classification time: {t_end - t_start:.4f}s")
