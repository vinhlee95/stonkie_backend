"""V2 comparison handler — per-ticker Brave fanout with concurrency cap."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, AsyncGenerator, Dict, List, Optional

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.brave_client import BraveClient
from connectors.company import CompanyConnector
from connectors.company_financial import CompanyFinancialConnector
from services.analysis_progress import AnalysisPhase, thinking_status
from services.analyze_retrieval.citation_index import build_sources_event
from services.analyze_retrieval.market import resolve_market
from services.analyze_retrieval.retrieval import retrieve_for_analyze
from services.analyze_retrieval.schemas import AnalyzeSource, BraveRetrievalError
from services.question_analyzer.context_builders.comparison_builder import (
    CompanyComparisonData,
    ComparisonCompanyBuilder,
    ComparisonCompanyBuilderInput,
)
from services.question_analyzer.context_builders.components import PromptComponents
from services.question_analyzer.handlers_v2 import (
    _BRAVE_CITATION_DIRECTIVE,
    _build_sources_block,
    _collect_answer_chunks,
    _trusted_publisher_status,
)
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

    async def _generate_related_questions(
        self,
        original_question: str,
        preferred_model: ModelName,
        short_analysis: bool = False,
    ) -> AsyncGenerator[Dict[str, str], None]:
        prompt = f"""
Based on this comparison question: "{original_question}"
Generate {2 if short_analysis else 3} follow-up comparison questions, one per line.
        """.strip()
        agent = MultiAgent(model_name=preferred_model)
        for q in agent.generate_content_by_lines(
            prompt=prompt,
            use_google_search=False,
            max_lines=2 if short_analysis else 3,
            min_line_length=10,
            strip_numbering=True,
            strip_markdown=True,
        ):
            yield {"type": "related_question", "body": q}

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
    ) -> tuple[str, list[AnalyzeSource], Optional[BraveRetrievalError]]:
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
                )
                return ticker, result.sources, None
            except BraveRetrievalError as e:
                return ticker, [], e

    async def handle(
        self,
        tickers: list[str],
        question: str,
        search_decision: SearchDecision,
        short_analysis: bool,
        preferred_model: ModelName,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        request_id: str = "request-unknown",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        tickers_str = ", ".join(tickers)
        yield thinking_status(
            f"Loading financial data for {tickers_str}...",
            phase=AnalysisPhase.DATA_FETCH,
            step=3,
            total_steps=5,
        )

        companies_data = await self._fetch_companies_parallel(tickers)
        if len(companies_data) < 2:
            yield {"type": "error", "body": "Need at least 2 companies for comparison."}
            return

        source_labels = {
            "database": "our database",
            "training_data": "general knowledge",
            "google_search": "web search",
        }
        origin_parts = [f"{c.ticker} from {source_labels.get(c.data_source, c.data_source)}" for c in companies_data]
        yield thinking_status(
            f"Comparing {', '.join(origin_parts)}",
            phase=AnalysisPhase.ANALYZE,
            step=4,
            total_steps=5,
        )

        google_search_tickers = [c.ticker for c in companies_data if c.data_source == "google_search"]
        use_google_search = search_decision.use_google_search or bool(google_search_tickers)

        retrieved_per_ticker: list[tuple[str, list[AnalyzeSource]]] = []
        failed_tickers: list[str] = []
        if use_google_search:
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
                    question=question,
                    market=resolve_market(country_by_ticker.get(c.ticker), question),
                    request_id=request_id,
                    sem=sem,
                    company_name=_comparison_company_name(c),
                )
                for c in companies_data
            ]
            results = await asyncio.gather(*coros)
            for ticker, sources, err in results:
                if err is not None:
                    failed_tickers.append(ticker)
                else:
                    retrieved_per_ticker.append((ticker, sources))

            if not retrieved_per_ticker:
                raise BraveRetrievalError(f"All ticker retrievals failed: {', '.join(failed_tickers)}")

        # Flat-concatenate sources in ticker order
        flat_sources: list[AnalyzeSource] = []
        for _ticker, sources in retrieved_per_ticker:
            flat_sources.extend(sources)

        if flat_sources:
            successful_tickers = [t for t, _s in retrieved_per_ticker]
            status_event = _trusted_publisher_status(flat_sources, ticker_list=successful_tickers)
            if status_event is not None:
                yield status_event

        if failed_tickers:
            yield thinking_status(
                f"Web retrieval failed for {', '.join(failed_tickers)} — proceeding with the rest",
                phase=AnalysisPhase.SEARCH,
                step=2,
                total_steps=5,
            )

        builder_input = ComparisonCompanyBuilderInput(
            tickers=[c.ticker for c in companies_data],
            question=question,
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

        if flat_sources:
            prompt += "\n\n" + _BRAVE_CITATION_DIRECTIVE
        prompt += _build_sources_block(flat_sources)

        agent = MultiAgent(model_name=preferred_model)
        for chunk in _collect_answer_chunks(agent.generate_content(prompt=prompt, use_google_search=False)):
            yield {"type": "answer", "body": chunk}

        if flat_sources or use_google_search:
            yield build_sources_event(flat_sources)

        yield {"type": "model_used", "body": agent.model_name}

        # v1 provenance event preserved alongside v2 sources
        yield {
            "type": "data_sources",
            "body": [{"name": c.ticker, "source": c.data_source} for c in companies_data],
        }

        async for related_q in self._generate_related_questions(question, preferred_model, short_analysis):
            yield related_q
