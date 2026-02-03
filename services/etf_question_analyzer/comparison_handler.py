"""Handler for ETF comparison questions."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict

from langfuse import get_client, observe

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.etf_fundamental import ETFFundamentalConnector

from .context_builders.comparison_builder import ComparisonContextBuilderInput, ComparisonETFBuilder

logger = logging.getLogger(__name__)
langfuse = get_client()


class ETFComparisonHandler:
    """Handles multi-ETF comparison questions."""

    def __init__(self):
        self.connector = ETFFundamentalConnector()
        self.builder = ComparisonETFBuilder()

    @observe(name="etf_comparison_handler")
    async def handle(
        self,
        tickers: list[str],
        question: str,
        use_google_search: bool,
        preferred_model: ModelName,
        conversation_messages: list | None = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Handle ETF comparison questions.

        Args:
            tickers: List of 2-4 ETF tickers to compare
            question: The comparison question
            use_google_search: Whether to use Google Search
            preferred_model: Preferred model to use
            conversation_messages: Conversation history

        Yields:
            Dictionary chunks with comparison analysis results
        """
        t_start = time.perf_counter()

        try:
            yield {"type": "thinking_status", "body": f"Fetching {len(tickers)} ETFs..."}

            # Fetch all ETFs in parallel
            t_fetch = time.perf_counter()
            etf_data_list = await self._fetch_etfs_parallel(tickers)
            t_fetch_end = time.perf_counter()
            logger.info(f"Fetched {len(etf_data_list)}/{len(tickers)} ETFs in {t_fetch_end - t_fetch:.4f}s")

            # Check if we have enough ETFs for comparison
            if len(etf_data_list) < 2:
                missing = set(tickers) - {etf.ticker for etf in etf_data_list}
                yield {
                    "type": "error",
                    "body": f"Need at least 2 valid ETFs for comparison. Missing: {', '.join(missing)}",
                }
                return

            # Warn about missing ETFs
            if len(etf_data_list) < len(tickers):
                found_tickers = {etf.ticker for etf in etf_data_list}
                missing_tickers = set(tickers) - found_tickers
                yield {
                    "type": "thinking_status",
                    "body": f"ETFs not found: {', '.join(missing_tickers)}. Comparing available ETFs...",
                }

            yield {"type": "thinking_status", "body": "Building comparison analysis..."}

            # Build comparison context
            builder_input = ComparisonContextBuilderInput(
                tickers=[etf.ticker for etf in etf_data_list],
                question=question,
                etf_data_list=etf_data_list,
                use_google_search=use_google_search,
            )
            prompt = self.builder.build(builder_input)

            # Generate comparison with LLM
            agent = MultiAgent(model_name=preferred_model)
            model_used = agent.model_name

            with langfuse.start_as_current_observation(
                as_type="generation", name="etf-comparison-llm-call", model=model_used
            ) as gen:
                gen.update(
                    input={
                        "prompt": prompt,
                        "tickers": [etf.ticker for etf in etf_data_list],
                        "model": model_used,
                    }
                )

                first_chunk_received = False
                full_output = []

                for text_chunk in agent.generate_content(prompt=prompt, use_google_search=use_google_search):
                    if not first_chunk_received:
                        gen.update(completion_start_time=datetime.now(timezone.utc))
                        first_chunk_received = True

                    yield {"type": "answer", "body": text_chunk}
                    full_output.append(text_chunk)

                gen.update(output="".join(full_output))

            yield {"type": "model_used", "body": model_used}

            # Generate comparison-specific follow-up questions
            async for related_q in self._generate_comparison_related_questions(question, preferred_model):
                yield related_q

            t_end = time.perf_counter()
            logger.info(f"ETFComparisonHandler total: {t_end - t_start:.4f}s")

        except Exception as e:
            logger.error(f"Error in ETFComparisonHandler: {e}")
            logger.exception("Comparison handler exception")
            yield {"type": "answer", "body": "Error generating comparison. Please try again later."}

    async def _fetch_etfs_parallel(self, tickers: list[str]) -> list:
        """
        Fetch multiple ETFs in parallel.

        Args:
            tickers: List of ticker symbols

        Returns:
            List of ETFFundamentalDto objects (may be fewer than input if some not found)
        """

        async def fetch_async(ticker: str):
            """Async wrapper for synchronous connector."""
            return await asyncio.to_thread(self.connector.get_by_ticker, ticker)

        # Fetch all in parallel
        results = await asyncio.gather(*[fetch_async(ticker) for ticker in tickers], return_exceptions=True)

        # Filter out None and exceptions
        valid_etfs = []
        for ticker, result in zip(tickers, results):
            if isinstance(result, Exception):
                logger.error(f"Error fetching {ticker}: {result}")
            elif result is not None:
                valid_etfs.append(result)
            else:
                logger.warning(f"ETF not found: {ticker}")

        return valid_etfs

    @observe(name="generate_comparison_related_questions")
    async def _generate_comparison_related_questions(
        self, original_question: str, preferred_model: ModelName = ModelName.Auto
    ) -> AsyncGenerator[Dict[str, str], None]:
        """
        Generate comparison-specific follow-up questions.

        Args:
            original_question: The original comparison question
            preferred_model: Preferred model to use

        Yields:
            Dictionary with type "related_question" and body
        """
        try:
            prompt = f"""
                Based on this ETF comparison question: "{original_question}"

                Generate exactly 3 high-quality follow-up questions for comparing ETFs.

                Requirements:
                - Question 1: Deeper comparison on same aspect (e.g., if asked about TER, ask about total cost of ownership)
                - Question 2: Compare different dimension (e.g., if cost comparison, ask about holdings overlap or performance)
                - Question 3: Investment decision question (e.g., "Which is better for long-term investing?")
                - Keep questions 8-15 words
                - Make them specific and actionable
                - Focus on comparative aspects

                Example dimensions for comparison:
                - Costs: TER, fund size, trading costs, expense differences
                - Holdings: overlap, concentration, unique positions, sector differences
                - Performance: historical returns, volatility, tracking difference comparison
                - Structure: replication method differences, liquidity, domicile benefits
                - Risk: geographic concentration, sector allocation, correlation
                - Suitability: which for what goal, tax efficiency, portfolio fit

                Output format (one question per line, no numbering):
                Which ETF has lower total cost of ownership including trading costs?
                How much overlap is there in their top holdings?
                Which is better for tax-efficient long-term investing?
            """

            agent = MultiAgent(model_name=preferred_model)

            for question in agent.generate_content_by_lines(
                prompt=prompt,
                use_google_search=False,
                max_lines=3,
                min_line_length=10,
                strip_numbering=True,
                strip_markdown=True,
            ):
                yield {"type": "related_question", "body": question}

        except Exception as e:
            logger.error(f"Error generating comparison related questions: {e}")
