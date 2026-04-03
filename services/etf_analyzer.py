"""ETF analyzer service - main entry point for ETF question analysis."""

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from langfuse import observe

from ai_models.model_name import ModelName
from services.search_decision_engine import SearchDecisionEngine
from utils.visual_stream import VisualAnswerStreamSplitter

from .etf_question_analyzer.classifier import ETFQuestionClassifier
from .etf_question_analyzer.comparison_handler import ETFComparisonHandler
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

    def __init__(self, search_decision_engine: Optional[SearchDecisionEngine] = None):
        self.search_decision_engine = search_decision_engine or SearchDecisionEngine()
        self.classifier = ETFQuestionClassifier()
        self.data_optimizer = ETFDataOptimizer()
        self.general_handler = GeneralETFHandler()
        self.overview_handler = ETFOverviewHandler()
        self.detailed_handler = ETFDetailedAnalysisHandler()
        self.comparison_handler = ETFComparisonHandler()

    @observe(name="etf_analyzer.analyze_question")
    async def analyze_question(
        self,
        ticker: str,
        question: str,
        use_url_context: bool = False,
        deep_analysis: bool = False,
        preferred_model: ModelName = ModelName.Auto,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        conversation_id: Optional[str] = None,
        anon_user_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        t_start = time.perf_counter()

        yield {"type": "thinking_status", "body": "Just a moment..."}

        normalized_ticker = ticker.strip().upper() if ticker else ""
        if normalized_ticker in ["UNDEFINED", "NULL", "NONE", ""]:
            normalized_ticker = ""

        if conversation_messages:
            num_pairs = len(conversation_messages) // 2
            logger.info(
                f"ETF conversation context: {num_pairs} Q/A pair(s) "
                f"(ticker: {normalized_ticker or 'general'}, question: {question[:50]}...)"
            )

        # Run search decision + ETF classification in parallel
        search_coro = self.search_decision_engine.decide(
            question=question,
            ticker=normalized_ticker,
            is_etf=True,
        )
        classify_coro = self.classifier.classify_question(normalized_ticker, question)

        t_parallel = time.perf_counter()
        decision, classify_result = await asyncio.gather(search_coro, classify_coro)
        question_type, data_requirement, comparison_tickers = classify_result
        t_parallel_end = time.perf_counter()
        logger.info(
            f"ETF parallel (search+classify): {t_parallel_end - t_parallel:.4f}s, "
            f"type={question_type.value}, data={data_requirement.value}"
        )

        use_google_search = decision.use_google_search

        # Yield search decision metadata
        yield {
            "type": "search_decision_meta",
            "body": {
                "search_decision": "on" if use_google_search else "off",
                "reason_code": decision.reason_code,
                "decision_model": decision.decision_model,
                "decision_fallback": decision.decision_fallback,
                "confidence": decision.confidence,
            },
        }
        if use_google_search:
            yield {"type": "thinking_status", "body": "Using Google Search for up-to-date information..."}
        else:
            yield {"type": "thinking_status", "body": "Using internal knowledge/context for fastest response..."}

        # Handle comparison questions
        if question_type == ETFQuestionType.ETF_COMPARISON and comparison_tickers:
            short_analysis = not deep_analysis
            logger.info(f"Routing to comparison handler for tickers: {comparison_tickers}")
            comparison_splitter = VisualAnswerStreamSplitter()
            async for chunk in self.comparison_handler.handle(
                tickers=comparison_tickers,
                question=question,
                use_google_search=use_google_search,
                short_analysis=short_analysis,
                preferred_model=preferred_model,
                conversation_messages=conversation_messages,
            ):
                if chunk.get("type") == "answer" and isinstance(chunk.get("body"), str):
                    for visual_event in comparison_splitter.process_text(chunk["body"]):
                        yield visual_event
                else:
                    yield chunk
            for visual_event in comparison_splitter.finalize():
                yield visual_event
            return

        # Fetch optimized data
        t_data = time.perf_counter()
        etf_data = await self.data_optimizer.fetch_optimized_data(normalized_ticker, data_requirement)
        t_data_end = time.perf_counter()
        logger.info(f"ETF data fetch: {t_data_end - t_data:.4f}s")

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

        # Route to handler and track sources
        t_handler = time.perf_counter()
        has_sources = False
        visual_splitter = VisualAnswerStreamSplitter()

        if question_type == ETFQuestionType.GENERAL_ETF:
            handler_gen = self.general_handler.handle(context)
        elif question_type == ETFQuestionType.ETF_OVERVIEW:
            handler_gen = self.overview_handler.handle(context)
        elif question_type == ETFQuestionType.ETF_DETAILED_ANALYSIS:
            handler_gen = self.detailed_handler.handle(context)
        else:
            yield {"type": "answer", "body": "Unable to process question type"}
            return

        async for chunk in handler_gen:
            chunk_type = chunk.get("type")
            if chunk_type == "google_search_ground" and chunk.get("url"):
                has_sources = True
            elif chunk_type == "sources" and chunk.get("body"):
                has_sources = True
            elif chunk_type == "sources_grouped":
                grouped = (chunk.get("body") or {}).get("sources") if isinstance(chunk.get("body"), dict) else []
                if grouped:
                    has_sources = True
            if chunk_type == "answer" and isinstance(chunk.get("body"), str):
                for visual_event in visual_splitter.process_text(chunk.get("body")):
                    yield visual_event
            else:
                yield chunk

        for visual_event in visual_splitter.finalize():
            yield visual_event

        if use_google_search and not has_sources:
            logger.warning("ETF search attempt completed with no sources/citations.")
            yield {
                "type": "thinking_status",
                "body": "Live search returned no usable citations in this response. Information may be less current.",
            }

        t_handler_end = time.perf_counter()
        logger.info(f"ETF handler execution: {t_handler_end - t_handler:.4f}s")

        t_end = time.perf_counter()
        logger.info(f"ETF analyzer total: {t_end - t_start:.4f}s")
