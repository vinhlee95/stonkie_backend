"""V2 ETF comparison handler — ETF vs ETF, per-ticker Brave fan-out (asset_class="etf") with a concurrency cap.

Partial-failure semantics follow CompanyComparisonHandlerV2: a ticker whose retrieval fails is reported and
left out, the answer goes ahead with the rest, and only an all-tickers failure raises BraveRetrievalError.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, AsyncIterator, Callable, Dict, List, Optional

from ai_models.model_name import ModelName
from connectors.etf_fundamental import ETFFundamentalConnector, ETFFundamentalDto
from services.analysis_progress import AnalysisPhase, ThinkingProgress
from services.analyze_retrieval.citation_index import build_sources_event
from services.analyze_retrieval.retrieval import BraveSearchClient
from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeSource, BraveRetrievalError
from services.question_analyzer.context_builders.components import PromptComponents
from services.question_analyzer.handlers_v2 import (
    _build_prompt_debug_event,
    _build_sources_block,
    _iter_answer_chunks,
    _writing_status,
    generate_related_questions,
    related_questions_result,
    start_related_questions,
)
from services.question_analyzer.router import ETFRoutingResult

from .context_builders.comparison_builder import ComparisonContextBuilderInput, ComparisonETFBuilder
from .handlers_v2 import AgentFactory, Retriever, _ETFHandlerV2Base, _fund_query
from .ticker_extractor import dedupe_tickers

logger = logging.getLogger(__name__)

_CONCURRENCY_CAP = 5

# (original question, short_analysis) -> related_question events
ComparisonRelatedQuestions = Callable[[str, bool], AsyncIterator[Dict[str, str]]]


def _comparison_related_questions(original_question: str, short_analysis: bool) -> AsyncIterator[Dict[str, str]]:
    return generate_related_questions(original_question, comparison=True, count=2 if short_analysis else 3)


class ETFComparisonHandlerV2(_ETFHandlerV2Base):
    """Multi-ETF comparison v2 handler (2-4 ETFs, all resolved by ETFTickerExtractor)."""

    handler_name = "etf_comparison_v2"

    def __init__(
        self,
        connector: Optional[ETFFundamentalConnector] = None,
        retriever: Optional[Retriever] = None,
        brave_client: Optional[BraveSearchClient] = None,
        agent_factory: Optional[AgentFactory] = None,
        related_questions: Optional[ComparisonRelatedQuestions] = None,
    ):
        super().__init__(retriever=retriever, brave_client=brave_client, agent_factory=agent_factory)
        self._comparison_related_questions = related_questions or _comparison_related_questions
        self.connector = connector or ETFFundamentalConnector()
        self.builder = ComparisonETFBuilder()

    async def _fetch_etfs_parallel(self, tickers: list[str]) -> list[tuple[str, ETFFundamentalDto]]:
        """(requested ticker, DTO) for every ETF found, in request order; missing/failed ones are left out."""
        results = await asyncio.gather(
            *[asyncio.to_thread(self.connector.get_by_ticker, ticker) for ticker in tickers], return_exceptions=True
        )
        found: list[tuple[str, ETFFundamentalDto]] = []
        for ticker, result in zip(tickers, results):
            if isinstance(result, BaseException):
                logger.error("Error fetching ETF %s: %s", ticker, result)
            elif result is None:
                logger.warning("ETF not found: %s", ticker)
            else:
                found.append((ticker, result))
        return found

    async def _retrieve_one_ticker(
        self,
        etf: ETFFundamentalDto,
        ticker: str,
        question: str,
        request_id: str,
        sem: asyncio.Semaphore,
    ) -> tuple[str, list[AnalyzeSource], list[AnalyzePassage], Optional[BraveRetrievalError]]:
        async with sem:
            query = _fund_query(question, ticker=ticker, etf_name=etf.name)
            try:
                result = await asyncio.to_thread(
                    self._retrieve,
                    question,
                    [query],
                    ticker=ticker,
                    etf_name=etf.name,
                    request_id=request_id,
                )
                return ticker, result.sources, result.selected_passages, None
            except BraveRetrievalError as e:
                return ticker, [], [], e

    async def handle(
        self,
        *,
        tickers: list[str],
        question: str,
        routing: ETFRoutingResult,
        short_analysis: bool,
        preferred_model: ModelName = ModelName.Auto,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        request_id: str = "request-unknown",
        debug_prompt_context: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        # The router's standalone question carries a follow-up's topic into retrieval, and into the prompt
        # when there is history to resolve (same as CompanyComparisonHandlerV2).
        retrieval_question = routing.standalone_question
        tickers = dedupe_tickers(tickers)  # guard: never compare a fund with itself or retrieve it twice
        prompt_question = retrieval_question if conversation_messages else question

        progress = ThinkingProgress()
        yield progress.status(f"Loading data for {', '.join(tickers)}...", phase=AnalysisPhase.DATA_FETCH)

        found = await self._fetch_etfs_parallel(tickers)
        compared = [ticker for ticker, _etf in found]
        etfs = [etf for _ticker, etf in found]
        missing = [t for t in tickers if t not in compared]
        if len(found) < 2:
            yield {
                "type": "error",
                "body": f"Need at least 2 valid ETFs for comparison. Missing: {', '.join(missing)}",
            }
            return
        # Missing funds are folded into the next status rather than sent in the same instant on their own.
        missing_lead = f"Couldn't find {', '.join(missing)}, " if missing else ""

        retrieved_per_ticker: list[tuple[str, list[AnalyzeSource], list[AnalyzePassage]]] = []
        failed_tickers: list[str] = []
        if routing.search_decision.use_google_search:
            searching = f"searching the web for {', '.join(compared)}..."
            yield progress.status(
                f"{missing_lead}{searching}" if missing_lead else searching[0].upper() + searching[1:],
                phase=AnalysisPhase.SEARCH,
            )
            missing_lead = ""
            sem = asyncio.Semaphore(_CONCURRENCY_CAP)
            results = await asyncio.gather(
                *[self._retrieve_one_ticker(etf, ticker, retrieval_question, request_id, sem) for ticker, etf in found]
            )
            for ticker, sources, passages, err in results:
                if err is not None:
                    failed_tickers.append(ticker)
                else:
                    retrieved_per_ticker.append((ticker, sources, passages))

            if not retrieved_per_ticker:
                raise BraveRetrievalError(f"All ticker retrievals failed: {', '.join(failed_tickers)}")

        # Flat-concatenate sources in ticker order
        flat_sources: list[AnalyzeSource] = []
        flat_passages: list[AnalyzePassage] = []
        for _ticker, sources, passages in retrieved_per_ticker:
            flat_sources.extend(sources)
            flat_passages.extend(passages)

        prompt = self.builder.build(
            ComparisonContextBuilderInput(
                tickers=compared,
                question=prompt_question,
                etf_data_list=etfs,
                use_google_search=False,
                short_analysis=short_analysis,
                grounding=PromptComponents.grounding_rules(),
            )
        )
        if failed_tickers:
            prompt += (
                f"\n\nNote: web retrieval failed for {', '.join(failed_tickers)}. "
                f"Answer using available data only and acknowledge this limitation."
            )
        prompt += _build_sources_block(flat_sources, flat_passages)

        if debug_prompt_context:
            yield _build_prompt_debug_event(
                handler=self.handler_name,
                prompt=prompt,
                retrieved_sources=flat_sources,
                selected_passages=flat_passages,
            )

        yield _writing_status(
            progress,
            flat_sources,
            ticker_list=[t for t, _sources, _passages in retrieved_per_ticker],
            lead=f"No web results for {', '.join(failed_tickers)}, " if failed_tickers else missing_lead,
        )
        related_task = await start_related_questions(self._comparison_related_questions(question, short_analysis))
        try:
            agent = self._agent_factory(model_name=preferred_model)
            for chunk in _iter_answer_chunks(agent.generate_content(prompt=prompt, use_google_search=False)):
                yield {"type": "answer", "body": chunk}

            if flat_sources:
                yield build_sources_event(flat_sources)

            yield {"type": "model_used", "body": agent.model_name}

            for related_q in await related_questions_result(related_task):
                yield related_q
        finally:
            related_task.cancel()
