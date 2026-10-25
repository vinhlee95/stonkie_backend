"""ETF analyzer v2 service — QuestionRouter (ETF mode) + Brave-grounded ETF v2 handlers."""

from __future__ import annotations

import asyncio
import datetime
import logging
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from langfuse import observe
from langfuse._client.get_client import get_client as get_langfuse_client

from ai_models.model_name import ModelName
from connectors.etf_fundamental import ETFFundamentalDto
from services.analysis_progress import AnalysisPhase, ThinkingProgress
from services.etf_question_analyzer.comparison_handler_v2 import ETFComparisonHandlerV2
from services.etf_question_analyzer.data_optimizer import ETFDataOptimizer
from services.etf_question_analyzer.handlers_v2 import (
    ETFDetailedAnalysisHandlerV2,
    ETFOverviewHandlerV2,
    GeneralETFHandlerV2,
)
from services.etf_question_analyzer.ticker_extractor import ETFTickerExtractor, dedupe_tickers
from services.etf_question_analyzer.types import ETFDataRequirement, ETFQuestionType
from services.question_analyzer.router import (
    ETFRoutingResult,
    QuestionRouter,
    apply_etf_fast_paths,
    default_etf_routing,
)
from utils.chat_prompt import extract_answer_text

logger = logging.getLogger(__name__)

_INVALID_TICKERS = {"UNDEFINED", "NULL", "NONE", ""}


class ETFAnalyzerV2:
    """v2 analyzer for ETF tickers; one router call decides type, data level and search."""

    def __init__(
        self,
        router: Optional[QuestionRouter] = None,
        ticker_extractor: Optional[ETFTickerExtractor] = None,
        data_optimizer: Optional[ETFDataOptimizer] = None,
        general_handler: Optional[GeneralETFHandlerV2] = None,
        overview_handler: Optional[ETFOverviewHandlerV2] = None,
        detailed_handler: Optional[ETFDetailedAnalysisHandlerV2] = None,
        comparison_handler: Optional[ETFComparisonHandlerV2] = None,
        extractor_timeout_seconds: float = 5.0,
    ):
        self.extractor_timeout_seconds = extractor_timeout_seconds
        self.router = router or QuestionRouter()
        self.ticker_extractor = ticker_extractor or ETFTickerExtractor()
        self.data_optimizer = data_optimizer or ETFDataOptimizer()
        self.general_handler = general_handler or GeneralETFHandlerV2()
        self.overview_handler = overview_handler or ETFOverviewHandlerV2()
        self.detailed_handler = detailed_handler or ETFDetailedAnalysisHandlerV2()
        self.comparison_handler = comparison_handler or ETFComparisonHandlerV2()

    async def _route(
        self,
        *,
        question: str,
        ticker: str,
        conversation_messages: Optional[List[Dict[str, str]]],
        etf: Optional[ETFFundamentalDto],
    ) -> tuple[ETFRoutingResult, list[str]]:
        """One router LLM call, concurrent with the regex-gated comparison pre-check.

        Returns (routing, comparison_tickers). A router failure yields the default ETF routing (no LLM call).
        """
        router_task = asyncio.ensure_future(
            self.router.route_etf(
                question=question,
                ticker=ticker,
                conversation_messages=conversation_messages,
                etf_name=etf.name if etf else None,
                fund_provider=etf.fund_provider if etf else None,
                index_tracked=etf.index_tracked if etf else None,
            )
        )
        try:
            # Let the router hand its LLM call to a worker thread before the extractor (sync LLM calls) runs.
            await asyncio.sleep(0)
            comparison_tickers: list[str] = []
            try:
                comparison_tickers = await asyncio.wait_for(
                    self.ticker_extractor.extract_tickers(question, current_ticker=ticker or None),
                    timeout=self.extractor_timeout_seconds,
                )
            except TimeoutError:
                logger.warning(
                    "ETF ticker extraction timed out after %.1fs, continuing without comparison",
                    self.extractor_timeout_seconds,
                )
            except Exception as e:
                logger.error("Error in ETF ticker extraction, continuing without comparison: %s", e)
            routing = await router_task
        except BaseException:
            router_task.cancel()
            raise

        # The context rewrite can repeat the viewed fund ("Compare it with SXR8" on SXR8 -> [SXR8, SXR8]).
        comparison_tickers = dedupe_tickers(comparison_tickers)
        if routing is None:
            routing = default_etf_routing(question=question, ticker=ticker)
        else:
            routing = apply_etf_fast_paths(routing, question=question)
        return routing, comparison_tickers

    @observe(
        name="etf_analyzer_v2.analyze_question",
        as_type="generation",
        capture_input=False,
        transform_to_string=extract_answer_text,
    )
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
        debug_prompt_context: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        # use_url_context is not supported for ETFs.
        _ = (use_url_context, conversation_id, anon_user_id)
        langfuse = get_langfuse_client()
        if langfuse:
            langfuse.update_current_generation(input=question)
        ttft_recorded = False

        # Sent before the DB read and the router LLM call so the client shows progress at once.
        progress = ThinkingProgress()
        yield progress.status("Understanding your question...", phase=AnalysisPhase.CLASSIFY)

        normalized_ticker = ticker.strip().upper() if ticker else ""
        if normalized_ticker in _INVALID_TICKERS:
            normalized_ticker = ""

        # The fund's row gives the router its DB context; one read serves the handlers too (ETFDataOptimizer
        # returns the full row for BASIC and DETAILED alike).
        etf: Optional[ETFFundamentalDto] = None
        if normalized_ticker:
            etf = await self.data_optimizer.fetch_optimized_data(normalized_ticker, ETFDataRequirement.DETAILED)

        routing, comparison_tickers = await self._route(
            question=question,
            ticker=normalized_ticker,
            conversation_messages=conversation_messages,
            etf=etf,
        )

        request_id = str(uuid.uuid4())
        common = {
            "preferred_model": preferred_model,
            "conversation_messages": conversation_messages,
            "request_id": request_id,
            "debug_prompt_context": debug_prompt_context,
        }
        if len(comparison_tickers) >= 2:
            logger.info("Routing ETF question to comparison for tickers: %s", comparison_tickers)
            iterator = self.comparison_handler.handle(
                tickers=comparison_tickers,
                question=question,
                routing=routing,
                short_analysis=not deep_analysis,
                **common,
            )
        else:
            handler = {
                ETFQuestionType.GENERAL_ETF: self.general_handler,
                ETFQuestionType.ETF_OVERVIEW: self.overview_handler,
                ETFQuestionType.ETF_DETAILED_ANALYSIS: self.detailed_handler,
            }.get(routing.question_type)
            if handler is None:
                yield {"type": "answer", "body": f"❌ Unsupported question type: {routing.question_type}"}
                return
            iterator = handler.handle(
                ticker=normalized_ticker,
                question=question,
                etf_data=etf if routing.data_requirement != ETFDataRequirement.NONE else None,
                routing=routing,
                deep_analysis=deep_analysis,
                **common,
            )

        async for chunk in iterator:
            if not ttft_recorded and langfuse and chunk.get("type") == "answer" and isinstance(chunk.get("body"), str):
                langfuse.update_current_generation(completion_start_time=datetime.datetime.now())
                ttft_recorded = True
            yield progress.renumber(chunk)
