"""ETF analyzer service - main entry point for ETF question analysis."""

import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from langfuse import observe

from ai_models.model_name import ModelName

from .etf_question_analyzer.classifier import ETFQuestionClassifier
from .etf_question_analyzer.data_optimizer import ETFDataOptimizer
from .etf_question_analyzer.handlers import (
    ETFDetailedAnalysisHandler,
    ETFOverviewHandler,
    GeneralETFHandler,
)
from .etf_question_analyzer.types import ETFAnalysisContext, ETFQuestionType

logger = logging.getLogger(__name__)


class ETFAnalyzer:
    """Main service for analyzing ETF questions and generating insights."""

    def __init__(self):
        """Initialize the ETF analyzer."""
        # Initialize components
        self.classifier = ETFQuestionClassifier()
        self.data_optimizer = ETFDataOptimizer()

        # Initialize handlers
        self.general_handler = GeneralETFHandler()
        self.overview_handler = ETFOverviewHandler()
        self.detailed_handler = ETFDetailedAnalysisHandler()

    @observe(name="etf_analyzer.analyze_question")
    async def analyze_question(
        self,
        ticker: str,
        question: str,
        use_google_search: bool = False,
        use_url_context: bool = False,
        deep_analysis: bool = False,
        preferred_model: ModelName = ModelName.Auto,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        conversation_id: Optional[str] = None,
        anon_user_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Analyze an ETF question and generate insights.

        Args:
            ticker: ETF ticker symbol (e.g., 'SXR8', 'CSPX')
            question: The question to answer
            use_google_search: Whether to use Google Search
            use_url_context: Whether to use URL context
            deep_analysis: Whether to use detailed analysis
            preferred_model: Preferred model to use
            conversation_messages: Optional conversation history
            conversation_id: Conversation ID for tracking
            anon_user_id: Anonymous user ID

        Yields:
            Dictionary chunks with analysis results
        """
        t_start = time.perf_counter()

        yield {"type": "thinking_status", "body": "Just a moment..."}

        # Normalize ticker
        normalized_ticker = ticker.strip().upper() if ticker else ""
        if normalized_ticker in ["UNDEFINED", "NULL", "NONE", ""]:
            normalized_ticker = ""

        # Log conversation context
        if conversation_messages:
            num_pairs = len(conversation_messages) // 2
            logger.info(
                f"ETF conversation context: {num_pairs} Q/A pair(s) "
                f"(ticker: {normalized_ticker or 'general'}, question: {question[:50]}...)"
            )

        # Classify question
        t_classify = time.perf_counter()
        question_type, data_requirement, comparison_tickers = await self.classifier.classify_question(
            normalized_ticker, question
        )
        t_classify_end = time.perf_counter()
        logger.info(
            f"ETF question classified: {question_type.value}, data: {data_requirement.value} "
            f"({t_classify_end - t_classify:.4f}s)"
        )

        # Handle comparison questions (Phase 4 - TODO: Implement comparison handler)
        if question_type == ETFQuestionType.ETF_COMPARISON and comparison_tickers:
            logger.warning(f"ETF comparison detected but handler not yet implemented. Tickers: {comparison_tickers}")
            yield {
                "type": "error",
                "body": "ETF comparison feature coming soon. Currently handling single ETF questions only.",
            }
            return

        # Fetch optimized data
        t_data = time.perf_counter()
        etf_data = await self.data_optimizer.fetch_optimized_data(normalized_ticker, data_requirement)
        t_data_end = time.perf_counter()
        logger.info(f"ETF data fetch: {t_data_end - t_data:.4f}s")

        if use_google_search:
            yield {
                "type": "thinking_status",
                "body": "Using Google Search for up-to-date information...",
            }

        # Create analysis context
        context = ETFAnalysisContext(
            ticker=normalized_ticker,
            question=question,
            question_type=question_type,
            data_requirement=data_requirement,
            etf_data=etf_data,
            use_google_search=use_google_search,
            use_url_context=use_url_context,
            deep_analysis=deep_analysis,
            preferred_model=preferred_model,
            conversation_messages=conversation_messages,
            source_url=None,
        )

        # Route to appropriate handler
        t_handler = time.perf_counter()

        if question_type == ETFQuestionType.GENERAL_ETF:
            async for chunk in self.general_handler.handle(context):
                yield chunk
        elif question_type == ETFQuestionType.ETF_OVERVIEW:
            async for chunk in self.overview_handler.handle(context):
                yield chunk
        elif question_type == ETFQuestionType.ETF_DETAILED_ANALYSIS:
            async for chunk in self.detailed_handler.handle(context):
                yield chunk
        else:
            yield {"type": "answer", "body": "Unable to process question type"}
            return

        t_handler_end = time.perf_counter()
        logger.info(f"ETF handler execution: {t_handler_end - t_handler:.4f}s")

        t_end = time.perf_counter()
        logger.info(f"ETF analyzer total: {t_end - t_start:.4f}s")
