"""Handler for stock company comparison questions."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict

from langfuse import get_client, observe

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.company import CompanyConnector
from connectors.company_financial import CompanyFinancialConnector

from .context_builders.comparison_builder import (
    CompanyComparisonData,
    ComparisonCompanyBuilder,
    ComparisonCompanyBuilderInput,
)
from .context_builders.components import PromptComponents
from .handlers import _collect_paragraph_sources, _process_source_tags
from .types import AnalysisPhase, thinking_status

logger = logging.getLogger(__name__)
langfuse = get_client()


class CompanyComparisonHandler:
    """Handles multi-stock comparison questions."""

    def __init__(self):
        self.company_connector = CompanyConnector()
        self.financial_connector = CompanyFinancialConnector()
        self.builder = ComparisonCompanyBuilder()

    @observe(name="company_comparison_handler")
    async def handle(
        self,
        tickers: list[str],
        question: str,
        use_google_search: bool,
        short_analysis: bool,
        preferred_model: ModelName,
        conversation_messages: list | None = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Handle stock comparison questions.

        Yields:
            Dictionary chunks with comparison analysis results
        """
        t_start = time.perf_counter()
        logger.info(f"CompanyComparison - tickers={tickers}, short_analysis={short_analysis}")

        try:
            tickers_str = ", ".join(tickers)
            yield thinking_status(
                f"Fetching data for {tickers_str}...",
                phase=AnalysisPhase.DATA_FETCH,
                step=3,
                total_steps=5,
            )

            # Fetch all companies in parallel
            t_fetch = time.perf_counter()
            companies_data = await self._fetch_companies_parallel(tickers)
            t_fetch_end = time.perf_counter()
            logger.info(f"Fetched {len(companies_data)}/{len(tickers)} companies in {t_fetch_end - t_fetch:.4f}s")

            # Check minimum companies for comparison
            if len(companies_data) < 2:
                yield {
                    "type": "error",
                    "body": "Need at least 2 companies for comparison. Some tickers could not be found.",
                }
                return

            # Emit a single transparent status showing where each ticker's data comes from
            source_labels = {
                "database": "internal database",
                "training_data": "model training data (may not be current)",
                "google_search": "Google Search (live)",
            }
            data_origin_parts = [
                f"{c.ticker} from {source_labels.get(c.data_source, c.data_source)}" for c in companies_data
            ]
            yield thinking_status(
                f"Building comparison — {', '.join(data_origin_parts)}",
                phase=AnalysisPhase.ANALYZE,
                step=4,
                total_steps=5,
            )

            google_search_tickers = [c.ticker for c in companies_data if c.data_source == "google_search"]
            if google_search_tickers:
                use_google_search = True
                logger.info(
                    f"[comparison_handler] Auto-enabling Google Search for non-DB tickers: {google_search_tickers}"
                )

            # Build comparison context
            builder_input = ComparisonCompanyBuilderInput(
                tickers=[c.ticker for c in companies_data],
                question=question,
                companies_data=companies_data,
                use_google_search=use_google_search,
                short_analysis=short_analysis,
            )
            prompt = self.builder.build(builder_input)
            prompt += "\n\n" + PromptComponents.visual_output_instructions()

            # Generate comparison with LLM
            agent = MultiAgent(model_name=preferred_model)
            model_used = agent.model_name

            with langfuse.start_as_current_observation(
                as_type="generation", name="company-comparison-llm-call", model=model_used
            ) as gen:
                gen.update(
                    input={
                        "prompt": prompt,
                        "tickers": [c.ticker for c in companies_data],
                        "model": model_used,
                    }
                )

                first_chunk_received = False
                full_output = []

                raw_chunks = agent.generate_content(prompt=prompt, use_google_search=use_google_search)
                for event in _collect_paragraph_sources(_process_source_tags(raw_chunks)):
                    if not first_chunk_received:
                        gen.update(completion_start_time=datetime.now(timezone.utc))
                        first_chunk_received = True

                    yield event
                    if event.get("type") == "answer":
                        full_output.append(event["body"])

                gen.update(output="".join(full_output))

            yield {"type": "model_used", "body": model_used}

            # Emit data source provenance
            data_sources = [{"name": c.ticker, "source": c.data_source} for c in companies_data]
            yield {"type": "sources", "body": data_sources}

            # Generate comparison-specific follow-up questions
            async for related_q in self._generate_comparison_related_questions(
                question, preferred_model, short_analysis
            ):
                yield related_q

            t_end = time.perf_counter()
            logger.info(f"CompanyComparisonHandler total: {t_end - t_start:.4f}s")

        except Exception as e:
            logger.error(f"Error in CompanyComparisonHandler: {e}")
            logger.exception("Comparison handler exception")
            yield {"type": "answer", "body": "Error generating comparison. Please try again later."}

    async def _fetch_companies_parallel(self, tickers: list[str]) -> list[CompanyComparisonData]:
        """
        Fetch fundamental + quarterly data for multiple companies in parallel.

        Returns CompanyComparisonData with data_source="training_data" for tickers
        where fundamental data is not available in DB.
        """

        async def fetch_single(ticker: str) -> CompanyComparisonData:
            """Fetch data for a single ticker."""
            try:
                # Check if company exists in DB at all
                company = await asyncio.to_thread(self.company_connector.get_by_ticker, ticker)
                if not company:
                    logger.warning(f"Ticker {ticker} not in database, marking as google_search")
                    return CompanyComparisonData(ticker=ticker, data_source="google_search")

                fundamental = await asyncio.to_thread(self.company_connector.get_fundamental_data, ticker)

                if not fundamental:
                    logger.warning(f"No fundamental data for {ticker}, marking as training_data")
                    return CompanyComparisonData(ticker=ticker, data_source="training_data")

                # Fetch quarterly statements
                quarterly = await asyncio.to_thread(
                    self.financial_connector.get_company_quarterly_financial_statements_recent,
                    ticker,
                    3,
                )
                quarterly_dicts = [CompanyFinancialConnector.to_dict(stmt) for stmt in quarterly] if quarterly else []

                return CompanyComparisonData(
                    ticker=ticker,
                    fundamental=fundamental,
                    quarterly_statements=quarterly_dicts,
                    data_source="database",
                )
            except Exception as e:
                logger.error(f"Error fetching {ticker}: {e}")
                return CompanyComparisonData(ticker=ticker, data_source="training_data")

        results = await asyncio.gather(*[fetch_single(t) for t in tickers])
        for r in results:
            logger.info(f"[comparison_handler] {r.ticker}: data_source={r.data_source}")
        return list(results)

    @observe(name="generate_company_comparison_related_questions")
    async def _generate_comparison_related_questions(
        self,
        original_question: str,
        preferred_model: ModelName = ModelName.Auto,
        short_analysis: bool = False,
    ) -> AsyncGenerator[Dict[str, str], None]:
        """Generate comparison-specific follow-up questions."""
        try:
            num_questions = 2 if short_analysis else 3

            prompt = f"""
                Based on this stock comparison question: "{original_question}"

                Generate exactly {num_questions} high-quality follow-up questions for comparing stocks.

                Requirements:
                - Question 1: Deeper comparison on same aspect (e.g., if asked about margins, ask about margin trends)
                {"- Question 2: Investment decision question" if short_analysis else "- Question 2: Compare different dimension (e.g., if profitability, ask about valuation or growth)"}
                {"" if short_analysis else "- Question 3: Investment decision question (e.g., \"Which stock offers better value for long-term investors?\")"}
                - Keep questions 8-15 words
                - Make them specific and actionable
                - Focus on comparative aspects

                Example dimensions for comparison:
                - Valuation: P/E ratio, PEG, price-to-sales
                - Profitability: margins, ROE, ROIC
                - Growth: revenue growth, earnings growth, guidance
                - Financial health: debt levels, cash position, free cash flow
                - Competitive position: market share, moat, industry trends
                - Shareholder returns: dividends, buybacks, total return

                Output format (one question per line, no numbering):
                Which company has shown stronger revenue growth over recent quarters?
                {"Which stock is better positioned for long-term growth?" if short_analysis else "How do their debt-to-equity ratios compare?\nWhich stock is better positioned for long-term growth?"}
            """

            agent = MultiAgent(model_name=preferred_model)

            for question in agent.generate_content_by_lines(
                prompt=prompt,
                use_google_search=False,
                max_lines=num_questions,
                min_line_length=10,
                strip_numbering=True,
                strip_markdown=True,
            ):
                yield {"type": "related_question", "body": question}

        except Exception as e:
            logger.error(f"Error generating comparison related questions: {e}")
