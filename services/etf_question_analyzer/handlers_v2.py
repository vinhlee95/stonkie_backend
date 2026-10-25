"""V2 ETF question handlers — QuestionRouter-driven, Brave-grounded (asset_class="etf").

Mirrors services/question_analyzer/handlers_v2.py: the router's standalone question + queries drive Brave
retrieval (no reformulator LLM call), the answer is grounded on the retrieved passages and the ETF database row,
sources stream as a footer event, and related questions run alongside the answer.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import aclosing
from dataclasses import replace
from typing import Any, AsyncGenerator, AsyncIterator, Callable, Dict, List, Optional

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.brave_client import BraveClient
from connectors.etf_fundamental import ETFFundamentalDto
from services.analysis_progress import AnalysisPhase, thinking_status
from services.analyze_retrieval.citation_index import build_sources_event
from services.analyze_retrieval.freshness import build_temporal_context_block
from services.analyze_retrieval.query_reformulator import PrecomputedQueryReformulator
from services.analyze_retrieval.retrieval import BraveSearchClient, build_company_aware_query, retrieve_for_analyze
from services.analyze_retrieval.schemas import (
    AnalyzePassage,
    AnalyzeRetrievalResult,
    AnalyzeSource,
    BraveRetrievalError,
)
from services.question_analyzer.context_builders.components import PromptComponents
from services.question_analyzer.handlers_v2 import (
    _build_prompt_debug_event,
    _build_sources_block,
    _iter_answer_chunks,
    _searching_status,
    _select_grounding,
    _trusted_publisher_status,
    generate_related_questions,
    related_questions_result,
    start_related_questions,
)
from services.question_analyzer.router import ETFRoutingResult, trim_search_query
from utils.conversation_format import format_conversation_context

from .context_builders import ETFContextBuilderInput, get_etf_context_builder
from .types import ETFDataRequirement

logger = logging.getLogger(__name__)

Retriever = Callable[..., AnalyzeRetrievalResult]
AgentFactory = Callable[..., Any]
RelatedQuestions = Callable[[str], AsyncIterator[Dict[str, str]]]
# Prompt prefix built once retrieval is done: (retrieved sources) -> builder output.
PromptBuilder = Callable[[List[AnalyzeSource]], str]

_ETF_MARKET = "GLOBAL"


def _etf_search_queries(routing: ETFRoutingResult, *, ticker: str | None, etf_name: str | None) -> list[str]:
    """Router queries, or one built from the standalone question + fund name when the router gave none
    (router search off but the handler forces search, or default routing after a router failure)."""
    if routing.search_queries:
        return list(routing.search_queries)
    return [_fund_query(routing.standalone_question, ticker=ticker, etf_name=etf_name)]


def _fund_query(question: str, *, ticker: str | None, etf_name: str | None) -> str:
    """Short search query: '<fund name> <TICKER> <question>' (ticker alone when the fund isn't in the DB), capped
    like the router's own queries so a long standalone question or fund name can't exceed Brave's limits."""
    if etf_name:
        return trim_search_query(build_company_aware_query(question, ticker=ticker, company_name=etf_name))
    return trim_search_query(build_company_aware_query(question, company_name=ticker))


def _conversation_context(
    conversation_messages: Optional[List[Dict[str, str]]], ticker: str | None, etf_name: str | None
) -> str:
    if not conversation_messages:
        return ""
    formatted = format_conversation_context(conversation_messages, ticker or "ETF", etf_name or "")
    return f"\n\n{formatted}\n" if formatted else ""


class _ETFHandlerV2Base:
    """Shared retrieval + answer streaming for the ETF v2 handlers."""

    handler_name = "etf_v2"

    def __init__(
        self,
        retriever: Optional[Retriever] = None,
        brave_client: Optional[BraveSearchClient] = None,
        agent_factory: Optional[AgentFactory] = None,
        related_questions: Optional[RelatedQuestions] = None,
    ):
        self._retriever = retriever or retrieve_for_analyze
        self._brave_client = brave_client
        self._agent_factory = agent_factory or MultiAgent
        self._related_questions = related_questions or generate_related_questions

    def _retrieve(
        self,
        question: str,
        queries: list[str],
        *,
        ticker: str | None,
        etf_name: str | None,
        request_id: str,
    ) -> AnalyzeRetrievalResult:
        """Brave retrieval with the ETF source policy (no reformulator LLM call). Blocking: run in a worker thread."""
        return self._retriever(
            question=question,
            market=_ETF_MARKET,
            request_id=request_id,
            brave_client=self._brave_client or BraveClient(api_key=os.getenv("BRAVE_API_KEY", "")),
            ticker=ticker,
            company_name=etf_name,
            query_reformulator=PrecomputedQueryReformulator(queries),
            asset_class="etf",
        )

    async def _stream_answer(
        self,
        *,
        ticker: str | None,
        question: str,
        etf_name: str | None,
        routing: ETFRoutingResult,
        use_search: bool,
        build_prompt: PromptBuilder,
        preferred_model: ModelName,
        conversation_messages: Optional[List[Dict[str, str]]],
        request_id: str,
        debug_prompt_context: bool,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        retrieved_sources: list[AnalyzeSource] = []
        selected_passages: list[AnalyzePassage] = []
        if use_search:
            queries = _etf_search_queries(routing, ticker=ticker, etf_name=etf_name)
            yield _searching_status(queries[0])
            try:
                retrieval_result = await asyncio.to_thread(
                    self._retrieve,
                    routing.standalone_question,
                    queries,
                    ticker=ticker,
                    etf_name=etf_name,
                    request_id=request_id,
                )
            except BraveRetrievalError:
                if routing.search_decision.use_google_search:
                    raise  # the router asked for search: the stream service reports retrieval_failed
                # The handler forced search to fill DB gaps; the DB row can still answer.
                logger.warning(
                    "ETF forced search failed; answering from the database",
                    exc_info=True,
                    extra={"request_id": request_id, "ticker": ticker},
                )
                retrieval_result = None
                yield thinking_status(
                    "No web sources found — answering from our database",
                    phase=AnalysisPhase.SEARCH,
                    step=2,
                    total_steps=4,
                )
            if retrieval_result is not None:
                retrieved_sources = retrieval_result.sources
                selected_passages = retrieval_result.selected_passages
                status_event = _trusted_publisher_status(retrieved_sources, passage_count=len(selected_passages))
                if status_event is not None:
                    yield status_event

        prompt = (
            build_prompt(retrieved_sources)
            + _conversation_context(conversation_messages, ticker, etf_name)
            + build_temporal_context_block(question)
            + _build_sources_block(retrieved_sources, selected_passages)
        )

        if debug_prompt_context:
            yield _build_prompt_debug_event(
                handler=self.handler_name,
                prompt=prompt,
                retrieved_sources=retrieved_sources,
                selected_passages=selected_passages,
            )

        related_task = await start_related_questions(self._related_questions(question))
        try:
            agent = self._agent_factory(model_name=preferred_model)
            for chunk in _iter_answer_chunks(agent.generate_content(prompt=prompt, use_google_search=False)):
                yield {"type": "answer", "body": chunk}

            if retrieved_sources:
                yield build_sources_event(retrieved_sources)

            yield {"type": "model_used", "body": agent.model_name}

            for related_q in await related_questions_result(related_task):
                yield related_q
        finally:
            related_task.cancel()


def _builder_input(
    *,
    ticker: str,
    question: str,
    etf_data: Optional[ETFFundamentalDto],
    deep_analysis: bool,
    grounding: str,
) -> ETFContextBuilderInput:
    return ETFContextBuilderInput(
        ticker=ticker,
        question=question,
        etf_data=etf_data,
        use_google_search=False,
        deep_analysis=deep_analysis,
        grounding=grounding,
    )


class GeneralETFHandlerV2(_ETFHandlerV2Base):
    """General ETF education questions (no fund data)."""

    handler_name = "general_etf_v2"

    async def handle(
        self,
        *,
        ticker: str,
        question: str,
        etf_data: Optional[ETFFundamentalDto],
        routing: ETFRoutingResult,
        preferred_model: ModelName = ModelName.Auto,
        deep_analysis: bool = False,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        request_id: str = "request-unknown",
        debug_prompt_context: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        yield thinking_status("Writing your answer...", phase=AnalysisPhase.ANALYZE, step=3, total_steps=4)

        # Without search, a general ETF question is an education answer: never the "no current data" decline.
        decision = routing.search_decision
        if not decision.use_google_search:
            decision = replace(decision, reason_code="stable_concept")

        def build_prompt(retrieved_sources: List[AnalyzeSource]) -> str:
            grounding = _select_grounding(retrieved_sources, decision)
            builder = get_etf_context_builder(ETFDataRequirement.NONE)
            return builder.build(
                _builder_input(
                    ticker=ticker, question=question, etf_data=None, deep_analysis=deep_analysis, grounding=grounding
                )
            )

        async with aclosing(
            self._stream_answer(
                ticker=ticker or None,
                question=question,
                etf_name=etf_data.name if etf_data else None,
                routing=routing,
                use_search=routing.search_decision.use_google_search,
                build_prompt=build_prompt,
                preferred_model=preferred_model,
                conversation_messages=conversation_messages,
                request_id=request_id,
                debug_prompt_context=debug_prompt_context,
            )
        ) as events:
            async for event in events:
                yield event


class ETFOverviewHandlerV2(_ETFHandlerV2Base):
    """Basic facts about one ETF (core metadata). Searches when name/TER/provider are missing."""

    handler_name = "etf_overview_v2"

    async def handle(
        self,
        *,
        ticker: str,
        question: str,
        etf_data: Optional[ETFFundamentalDto],
        routing: ETFRoutingResult,
        preferred_model: ModelName = ModelName.Auto,
        deep_analysis: bool = False,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        request_id: str = "request-unknown",
        debug_prompt_context: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        etf_label = ticker or "this ETF"
        yield thinking_status(
            f"Loading {etf_label} fund data...", phase=AnalysisPhase.DATA_FETCH, step=3, total_steps=4
        )

        # v1 rule: missing core metadata forces search, overriding the router's search-off.
        data_complete = bool(etf_data and etf_data.name and etf_data.ter_percent is not None and etf_data.fund_provider)
        if not data_complete:
            yield thinking_status(
                f"Some {etf_label} data missing — searching online...",
                phase=AnalysisPhase.SEARCH,
                step=3,
                total_steps=5,
            )

        def build_prompt(_retrieved_sources: List[AnalyzeSource]) -> str:
            builder = get_etf_context_builder(ETFDataRequirement.BASIC)
            return builder.build(
                _builder_input(
                    ticker=ticker,
                    question=question,
                    etf_data=etf_data,
                    deep_analysis=deep_analysis,
                    grounding=PromptComponents.grounding_rules(),
                )
            )

        async with aclosing(
            self._stream_answer(
                ticker=ticker or None,
                question=question,
                etf_name=etf_data.name if etf_data else None,
                routing=routing,
                use_search=routing.search_decision.use_google_search or not data_complete,
                build_prompt=build_prompt,
                preferred_model=preferred_model,
                conversation_messages=conversation_messages,
                request_id=request_id,
                debug_prompt_context=debug_prompt_context,
            )
        ) as events:
            async for event in events:
                yield event


class ETFDetailedAnalysisHandlerV2(_ETFHandlerV2Base):
    """Holdings / sector / country analysis of one ETF. Searches when any of those arrays is empty."""

    handler_name = "etf_detailed_analysis_v2"

    async def handle(
        self,
        *,
        ticker: str,
        question: str,
        etf_data: Optional[ETFFundamentalDto],
        routing: ETFRoutingResult,
        preferred_model: ModelName = ModelName.Auto,
        deep_analysis: bool = False,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        request_id: str = "request-unknown",
        debug_prompt_context: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        etf_label = ticker or "this ETF"
        total_steps = 5
        yield thinking_status(
            f"Reviewing {etf_label} holdings...", phase=AnalysisPhase.DATA_FETCH, step=3, total_steps=total_steps
        )

        has_holdings = bool(etf_data and etf_data.holdings)
        has_sectors = bool(etf_data and etf_data.sector_allocation)
        has_countries = bool(etf_data and etf_data.country_allocation)
        if not has_holdings or not has_sectors:
            total_steps = 6
            missing = "holdings" if not has_holdings else "sector data"
            yield thinking_status(
                f"{etf_label} {missing} not in database — searching online...",
                phase=AnalysisPhase.SEARCH,
                step=4,
                total_steps=total_steps,
            )
        # v1 rule: any empty array forces search, overriding the router's search-off.
        arrays_empty = not has_holdings or not has_sectors or not has_countries

        yield thinking_status(
            f"Analyzing {etf_label} in detail...",
            phase=AnalysisPhase.ANALYZE,
            step=total_steps - 1,
            total_steps=total_steps,
        )

        def build_prompt(_retrieved_sources: List[AnalyzeSource]) -> str:
            builder = get_etf_context_builder(ETFDataRequirement.DETAILED)
            return builder.build(
                _builder_input(
                    ticker=ticker,
                    question=question,
                    etf_data=etf_data,
                    deep_analysis=deep_analysis,
                    grounding=PromptComponents.grounding_rules(),
                )
            )

        async with aclosing(
            self._stream_answer(
                ticker=ticker or None,
                question=question,
                etf_name=etf_data.name if etf_data else None,
                routing=routing,
                use_search=routing.search_decision.use_google_search or arrays_empty,
                build_prompt=build_prompt,
                preferred_model=preferred_model,
                conversation_messages=conversation_messages,
                request_id=request_id,
                debug_prompt_context=debug_prompt_context,
            )
        ) as events:
            async for event in events:
                yield event
