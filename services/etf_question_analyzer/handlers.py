"""ETF question handlers for different types of ETF questions."""

import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict

from langfuse import get_client, observe

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName

from .context_builders import ETFContextBuilderInput, get_etf_context_builder
from .types import ETFAnalysisContext, ETFDataRequirement

logger = logging.getLogger(__name__)
langfuse = get_client()


class BaseETFHandler:
    """Base class for ETF question handlers."""

    @observe(name="generate_etf_related_questions")
    async def _generate_related_questions(
        self, original_question: str, preferred_model: ModelName = ModelName.Auto
    ) -> AsyncGenerator[Dict[str, str], None]:
        """
        Generate ETF-specific related follow-up questions.

        Args:
            original_question: The original question asked
            preferred_model: Preferred model to use

        Yields:
            Dictionary with type "related_question" and body
        """
        try:
            prompt = f"""
                Based on this ETF-related question: "{original_question}"

                Generate exactly 3 high-quality follow-up questions that a curious ETF investor might ask next.

                Requirements:
                - Question 1: Go deeper into the same ETF topic (e.g., if asked about TER, ask about tracking difference)
                - Question 2: Compare or contrast (e.g., compare to similar ETFs, compare accumulating vs distributing)
                - Question 3: Explore adjacent ETF topic (e.g., if asked about holdings, ask about sector allocation or rebalancing)
                - Keep questions 8-15 words
                - Make them specific and actionable
                - Use proper ETF terminology

                Example dimensions:
                - Holdings: concentration, top positions, turnover, rebalancing
                - Costs: TER, trading costs, bid-ask spread, tracking difference
                - Performance: returns, volatility, drawdowns, vs benchmark
                - Structure: replication method, fund size, liquidity, domicile
                - Allocation: sectors, countries, market cap, style factors
                - Distributions: dividend yield, distribution policy, tax efficiency

                Output format (one question per line, no numbering):
                How does this ETF's TER compare to competitors?
                What is the tracking difference for this ETF?
                What percentage is allocated to technology stocks?
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
            logger.error(f"Error generating ETF related questions: {e}")


class GeneralETFHandler(BaseETFHandler):
    """Handles general ETF education questions."""

    async def handle(self, context: ETFAnalysisContext) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Handle general ETF questions without specific ETF data.

        Args:
            context: ETF analysis context

        Yields:
            Dictionary chunks with analysis results
        """
        t_start = time.perf_counter()

        try:
            yield {"type": "thinking_status", "body": "Analyzing question..."}

            # Build prompt using NoneETFBuilder
            builder = get_etf_context_builder(ETFDataRequirement.NONE, context.use_url_context)
            builder_input = ETFContextBuilderInput(
                ticker=context.ticker,
                question=context.question,
                etf_data=None,
                use_google_search=context.use_google_search,
                deep_analysis=context.deep_analysis,
                source_url=None,
            )
            prompt = builder.build(builder_input)

            agent = MultiAgent(model_name=context.preferred_model)
            model_used = agent.model_name

            with langfuse.start_as_current_observation(
                as_type="generation", name="general-etf-llm-call", model=model_used
            ) as gen:
                gen.update(
                    input={"prompt": prompt, "use_google_search": context.use_google_search, "model": model_used}
                )

                first_chunk_received = False
                full_output = []

                for text_chunk in agent.generate_content(prompt=prompt, use_google_search=context.use_google_search):
                    if not first_chunk_received:
                        gen.update(completion_start_time=datetime.now(timezone.utc))
                        first_chunk_received = True

                    yield {"type": "answer", "body": text_chunk}
                    full_output.append(text_chunk)

                gen.update(output="".join(full_output))

            yield {"type": "model_used", "body": model_used}

            async for related_q in self._generate_related_questions(context.question, context.preferred_model):
                yield related_q

            t_end = time.perf_counter()
            logger.info(f"GeneralETFHandler total: {t_end - t_start:.4f}s")

        except Exception as e:
            logger.error(f"Error in GeneralETFHandler: {e}")
            yield {"type": "answer", "body": "Error generating answer. Please try again later."}


class ETFOverviewHandler(BaseETFHandler):
    """Handles basic ETF information questions."""

    async def handle(self, context: ETFAnalysisContext) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Handle ETF overview questions using core metadata.

        Args:
            context: ETF analysis context

        Yields:
            Dictionary chunks with analysis results
        """
        t_start = time.perf_counter()

        try:
            yield {"type": "thinking_status", "body": "Fetching ETF data..."}

            # Check data completeness
            data_complete = bool(
                context.etf_data
                and context.etf_data.name
                and context.etf_data.ter_percent is not None
                and context.etf_data.fund_provider
            )

            if not data_complete:
                yield {
                    "type": "thinking_status",
                    "body": "ETF data incomplete, searching for additional information...",
                }

            # Build prompt using BasicETFBuilder
            builder = get_etf_context_builder(ETFDataRequirement.BASIC, context.use_url_context)
            builder_input = ETFContextBuilderInput(
                ticker=context.ticker,
                question=context.question,
                etf_data=context.etf_data,
                use_google_search=context.use_google_search or not data_complete,
                deep_analysis=context.deep_analysis,
                source_url=context.source_url,
            )
            prompt = builder.build(builder_input)

            agent = MultiAgent(model_name=context.preferred_model)
            model_used = agent.model_name

            with langfuse.start_as_current_observation(
                as_type="generation", name="etf-overview-llm-call", model=model_used
            ) as gen:
                gen.update(input={"prompt": prompt, "ticker": context.ticker, "model": model_used})

                first_chunk_received = False
                full_output = []

                for text_chunk in agent.generate_content(
                    prompt=prompt, use_google_search=context.use_google_search or not data_complete
                ):
                    if not first_chunk_received:
                        gen.update(completion_start_time=datetime.now(timezone.utc))
                        first_chunk_received = True

                    yield {"type": "answer", "body": text_chunk}
                    full_output.append(text_chunk)

                gen.update(output="".join(full_output))

            yield {"type": "model_used", "body": model_used}

            async for related_q in self._generate_related_questions(context.question, context.preferred_model):
                yield related_q

            t_end = time.perf_counter()
            logger.info(f"ETFOverviewHandler total: {t_end - t_start:.4f}s")

        except Exception as e:
            logger.error(f"Error in ETFOverviewHandler: {e}")
            yield {"type": "answer", "body": "Error generating answer. Please try again later."}


class ETFDetailedAnalysisHandler(BaseETFHandler):
    """Handles detailed ETF analysis questions."""

    async def handle(self, context: ETFAnalysisContext) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Handle detailed ETF analysis questions.

        Args:
            context: ETF analysis context

        Yields:
            Dictionary chunks with analysis results
        """
        t_start = time.perf_counter()

        try:
            yield {"type": "thinking_status", "body": "Analyzing holdings..."}

            # Check data availability
            has_holdings = bool(context.etf_data and context.etf_data.holdings)
            has_sectors = bool(context.etf_data and context.etf_data.sector_allocation)
            has_countries = bool(context.etf_data and context.etf_data.country_allocation)

            if not has_holdings:
                yield {"type": "thinking_status", "body": "Holdings data unavailable, searching online..."}
            elif not has_sectors:
                yield {"type": "thinking_status", "body": "Sector data unavailable, searching online..."}

            arrays_empty = not has_holdings or not has_sectors or not has_countries

            # Build prompt using DetailedETFBuilder
            builder = get_etf_context_builder(ETFDataRequirement.DETAILED, context.use_url_context)
            builder_input = ETFContextBuilderInput(
                ticker=context.ticker,
                question=context.question,
                etf_data=context.etf_data,
                use_google_search=context.use_google_search or arrays_empty,
                deep_analysis=context.deep_analysis,
                source_url=context.source_url,
            )
            prompt = builder.build(builder_input)

            yield {"type": "thinking_status", "body": "Examining sector allocation..."}

            agent = MultiAgent(model_name=context.preferred_model)
            model_used = agent.model_name

            with langfuse.start_as_current_observation(
                as_type="generation", name="etf-detailed-llm-call", model=model_used
            ) as gen:
                gen.update(input={"prompt": prompt, "ticker": context.ticker, "model": model_used})

                first_chunk_received = False
                full_output = []

                for text_chunk in agent.generate_content(
                    prompt=prompt, use_google_search=context.use_google_search or arrays_empty
                ):
                    if not first_chunk_received:
                        gen.update(completion_start_time=datetime.now(timezone.utc))
                        first_chunk_received = True

                    yield {"type": "answer", "body": text_chunk}
                    full_output.append(text_chunk)

                gen.update(output="".join(full_output))

            yield {"type": "model_used", "body": model_used}

            async for related_q in self._generate_related_questions(context.question, context.preferred_model):
                yield related_q

            t_end = time.perf_counter()
            logger.info(f"ETFDetailedAnalysisHandler total: {t_end - t_start:.4f}s")

        except Exception as e:
            logger.error(f"Error in ETFDetailedAnalysisHandler: {e}")
            yield {"type": "answer", "body": "Error generating answer. Please try again later."}
