"""V2 comparison handler — per-ticker Brave fanout with concurrency cap."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, AsyncGenerator, AsyncIterator, Dict, List, Optional

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.brave_client import BraveClient
from connectors.company import CompanyConnector
from connectors.company_financial import CompanyFinancialConnector
from services.analysis_progress import AnalysisPhase, ThinkingProgress
from services.analyze_retrieval.citation_index import build_sources_event
from services.analyze_retrieval.market import resolve_market
from services.analyze_retrieval.query_reformulator import QueryReformulator
from services.analyze_retrieval.relevance import merge_sources
from services.analyze_retrieval.retrieval import retrieve_for_analyze
from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeSource, BraveRetrievalError
from services.question_analyzer.context_builders.comparison_builder import (
    CompanyComparisonData,
    ComparisonCompanyBuilder,
    ComparisonCompanyBuilderInput,
)
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
from services.question_analyzer.router import RoutingResult
from services.search_decision_engine import SearchDecision

logger = logging.getLogger(__name__)

_CONCURRENCY_CAP = 5


def _comparison_company_name(company_data: CompanyComparisonData) -> str | None:
    fundamental = company_data.fundamental
    if isinstance(fundamental, dict):
        return fundamental.get("Name") or fundamental.get("name") or company_data.ticker
    return getattr(fundamental, "name", None) or getattr(fundamental, "company_name", None) or company_data.ticker


class CompanyComparisonHandlerV2:
    """Multi-ticker comparison v2 handler."""

    def __init__(
        self,
        company_connector: Optional[CompanyConnector] = None,
        financial_connector: Optional[CompanyFinancialConnector] = None,
    ):
        self.company_connector = company_connector or CompanyConnector()
        self.financial_connector = financial_connector or CompanyFinancialConnector()
        self.builder = ComparisonCompanyBuilder()

    def _generate_related_questions(
        self, original_question: str, short_analysis: bool = False
    ) -> AsyncIterator[Dict[str, str]]:
        return generate_related_questions(original_question, comparison=True, count=2 if short_analysis else 3)

    async def _fetch_companies_parallel(self, tickers: list[str]) -> list[CompanyComparisonData]:
        async def fetch_single(ticker: str) -> CompanyComparisonData:
            try:
                company = await asyncio.to_thread(self.company_connector.get_by_ticker, ticker)
                if not company:
                    return CompanyComparisonData(ticker=ticker, data_source="google_search")
                fundamental = await asyncio.to_thread(self.company_connector.get_fundamental_data, ticker)
                if not fundamental:
                    return CompanyComparisonData(ticker=ticker, data_source="training_data")
                quarterly = await asyncio.to_thread(
                    self.financial_connector.get_company_quarterly_financial_statements_recent,
                    ticker,
                    3,
                )
                quarterly_dicts = [CompanyFinancialConnector.to_dict(s) for s in quarterly] if quarterly else []
                return CompanyComparisonData(
                    ticker=ticker,
                    fundamental=fundamental,
                    quarterly_statements=quarterly_dicts,
                    data_source="database",
                )
            except Exception as e:
                logger.error(f"Error fetching {ticker}: {e}")
                return CompanyComparisonData(ticker=ticker, data_source="training_data")

        return list(await asyncio.gather(*[fetch_single(t) for t in tickers]))

    async def _retrieve_one_ticker(
        self,
        ticker: str,
        question: str,
        market: str,
        request_id: str,
        sem: asyncio.Semaphore,
        company_name: str | None = None,
    ) -> tuple[str, list[AnalyzeSource], list[AnalyzePassage], Optional[BraveRetrievalError]]:
        async with sem:
            brave_client = BraveClient(api_key=os.getenv("BRAVE_API_KEY", ""))
            try:
                result = await asyncio.to_thread(
                    retrieve_for_analyze,
                    question=question,
                    market=market,
                    request_id=request_id,
                    brave_client=brave_client,
                    ticker=ticker,
                    company_name=company_name,
                    query_reformulator=QueryReformulator(),
                )
                return ticker, result.sources, result.selected_passages, None
            except BraveRetrievalError as e:
                return ticker, [], [], e

    async def handle(
        self,
        tickers: list[str],
        question: str,
        search_decision: SearchDecision,
        short_analysis: bool,
        preferred_model: ModelName,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        request_id: str = "request-unknown",
        debug_prompt_context: bool = False,
        routing: Optional[RoutingResult] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        # The router's standalone question carries a follow-up's topic ("How does that compare with AMD?")
        # into retrieval, and into the prompt when there is history to resolve.
        retrieval_question = routing.standalone_question if routing is not None else question
        prompt_question = retrieval_question if conversation_messages else question
        progress = ThinkingProgress()
        yield progress.status(f"Loading financial data for {', '.join(tickers)}...", phase=AnalysisPhase.DATA_FETCH)

        companies_data = await self._fetch_companies_parallel(tickers)
        if len(companies_data) < 2:
            yield {"type": "error", "body": "Need at least 2 companies for comparison."}
            return

        google_search_tickers = [c.ticker for c in companies_data if c.data_source == "google_search"]
        use_google_search = search_decision.use_google_search or bool(google_search_tickers)

        retrieved_per_ticker: list[tuple[str, list[AnalyzeSource], list[AnalyzePassage]]] = []
        failed_tickers: list[str] = []
        if use_google_search:
            yield progress.status(
                f"Searching the web for {', '.join(c.ticker for c in companies_data)}...", phase=AnalysisPhase.SEARCH
            )
            sem = asyncio.Semaphore(_CONCURRENCY_CAP)
            country_by_ticker: dict[str, Optional[str]] = {}
            for c in companies_data:
                country = None
                if c.fundamental:
                    country = (
                        getattr(c.fundamental, "country", None)
                        if not isinstance(c.fundamental, dict)
                        else (c.fundamental.get("Country") or c.fundamental.get("country"))
                    )
                country_by_ticker[c.ticker] = country

            coros = [
                self._retrieve_one_ticker(
                    ticker=c.ticker,
                    question=retrieval_question,
                    market=resolve_market(country_by_ticker.get(c.ticker), question),
                    request_id=request_id,
                    sem=sem,
                    company_name=_comparison_company_name(c),
                )
                for c in companies_data
            ]
            results = await asyncio.gather(*coros)
            for ticker, sources, passages, err in results:
                if err is not None:
                    failed_tickers.append(ticker)
                else:
                    retrieved_per_ticker.append((ticker, sources, passages))

            if not retrieved_per_ticker:
                raise BraveRetrievalError(f"All ticker retrievals failed: {', '.join(failed_tickers)}")

        # Concatenate sources in ticker order; one article found for several tickers is listed once
        flat_sources, flat_passages = merge_sources(
            [(sources, passages) for _ticker, sources, passages in retrieved_per_ticker]
        )

        builder_input = ComparisonCompanyBuilderInput(
            tickers=[c.ticker for c in companies_data],
            question=prompt_question,
            companies_data=companies_data,
            use_google_search=use_google_search,
            short_analysis=short_analysis,
        )
        prompt = self.builder.build(builder_input)
        prompt += "\n\n" + PromptComponents.visual_output_instructions()

        if failed_tickers:
            prompt += (
                f"\n\nNote: web retrieval failed for {', '.join(failed_tickers)}. "
                f"Answer using available data only and acknowledge this limitation."
            )

        prompt += _build_sources_block(flat_sources, flat_passages)

        if debug_prompt_context:
            yield _build_prompt_debug_event(
                handler="company_comparison_v2",
                prompt=prompt,
                retrieved_sources=flat_sources,
                selected_passages=flat_passages,
            )

        yield _writing_status(
            progress,
            flat_sources,
            ticker_list=[t for t, _sources, _passages in retrieved_per_ticker],
            lead=f"No web results for {', '.join(failed_tickers)}, " if failed_tickers else "",
        )
        related_task = await start_related_questions(self._generate_related_questions(question, short_analysis))
        try:
            agent = MultiAgent(model_name=preferred_model)
            for chunk in _iter_answer_chunks(agent.generate_content(prompt=prompt, use_google_search=False)):
                yield {"type": "answer", "body": chunk}

            if flat_sources or use_google_search:
                yield build_sources_event(flat_sources)

            yield {"type": "model_used", "body": agent.model_name}

            # data_sources provenance event (DB vs training data per ticker) alongside the Brave sources
            yield {
                "type": "data_sources",
                "body": [{"name": c.ticker, "source": c.data_source} for c in companies_data],
            }

            for related_q in await related_questions_result(related_task):
                yield related_q
        finally:
            related_task.cancel()
