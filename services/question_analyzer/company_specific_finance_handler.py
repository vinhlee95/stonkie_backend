"""Company-specific financial analysis handler."""

import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from langfuse import get_client

from agent.agent import Agent
from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.company import CompanyConnector
from core.financial_statement_type import FinancialStatementType
from utils.conversation_format import format_conversation_context

from .classifier import QuestionClassifier
from .context_builders import ContextBuilderInput, get_context_builder
from .context_builders.components import PromptComponents
from .data_optimizer import FinancialDataOptimizer
from .handlers import BaseQuestionHandler, _collect_paragraph_sources, _process_source_tags
from .types import AnalysisPhase, FinancialDataRequirement, thinking_status

logger = logging.getLogger(__name__)
langfuse = get_client()


class CompanySpecificFinanceHandler(BaseQuestionHandler):
    """Handles company-specific financial analysis questions."""

    def __init__(
        self,
        agent: Optional[Agent] = None,
        company_connector: Optional[CompanyConnector] = None,
        data_optimizer: Optional[FinancialDataOptimizer] = None,
        classifier: Optional[QuestionClassifier] = None,
    ):
        """
        Initialize the handler.

        Args:
            agent: AI agent for generating responses
            company_connector: Connector for company data
            data_optimizer: Optimizer for fetching financial data
            classifier: Classifier for analyzing questions
        """
        super().__init__(agent, company_connector)
        self.data_optimizer = data_optimizer or FinancialDataOptimizer()
        self.classifier = classifier or QuestionClassifier()

    async def handle(
        self,
        ticker: str,
        question: str,
        use_google_search: bool,
        use_url_context: bool,
        deep_analysis: bool = False,
        preferred_model: ModelName = ModelName.Auto,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        available_metrics: Optional[list[str]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Handle company-specific financial questions.

        Args:
            ticker: Company ticker symbol
            question: The question to answer
            use_google_search: Whether to use Google Search
            use_url_context: Whether to use URL context
            deep_analysis: Whether to use detailed analysis prompt (default: False for shorter responses)
            preferred_model: Preferred model to use for answer generation
            conversation_messages: Optional list of previous conversation messages for context

        Yields:
            Dictionary chunks with analysis results
        """
        t_start = time.perf_counter()
        ticker = ticker.lower().strip()

        # Fallback: If ticker is missing/undefined and we have conversation context, answer generally
        if (not ticker or ticker in ["undefined", "null", "none", ""]) and conversation_messages:
            logger.info(
                "⚠️  Fallback: Ticker is missing/undefined but conversation context exists. "
                "Answering question generally based on conversation context."
            )
            yield thinking_status(
                "Continuing from our previous conversation...",
                phase=AnalysisPhase.ANALYZE,
                step=3,
                total_steps=4,
            )

            # Use conversation context to answer generally
            company_name = ""
            conversation_context = format_conversation_context(
                conversation_messages, ticker or "the company", company_name
            )
            prompt = f"""{PromptComponents.current_date()}

Based on our previous conversation, answer this follow-up question:

{conversation_context}

Current question: {question}

IMPORTANT: Always respond in the same language as the CURRENT question above, regardless of the language used in previous conversation history.
Provide a helpful, general answer that builds on what we discussed before. If this is about financial strategy or concepts, explain it in general terms without requiring specific company financial data."""

            agent = MultiAgent(model_name=preferred_model)
            model_used = agent.model_name

            raw_chunks = agent.generate_content(prompt=prompt, use_google_search=use_google_search)
            for event in _process_source_tags(raw_chunks):
                yield event

            yield {"type": "model_used", "body": model_used}

            # Generate related questions
            async for related_q in self._generate_related_questions(question, preferred_model):
                yield related_q

            logger.info(
                f"Profiling CompanySpecificFinanceHandler total (fallback): {time.perf_counter() - t_start:.4f}s"
            )
            return

        # Determine what financial data we need (and which periods) in a single LLM call
        yield thinking_status(
            f"Figuring out what {ticker.upper()} data you need...",
            phase=AnalysisPhase.CLASSIFY,
            step=3,
            total_steps=6,
        )

        # Merged classifier: data_requirement + period_requirement in one LLM call
        (
            data_requirement,
            period_requirement,
            relevant_statements,
        ) = await self.classifier.classify_data_and_period_requirement(
            ticker, question, available_metrics=available_metrics
        )
        logger.info(f"Financial data requirement: {data_requirement}, period: {period_requirement}")

        if period_requirement is not None:
            yield thinking_status(
                f"Loading {ticker.upper()} {period_requirement.period_type} financial reports...",
                phase=AnalysisPhase.DATA_FETCH,
                step=4,
                total_steps=6,
            )

        # Fetch financial data
        (
            company_fundamental,
            annual_statements,
            quarterly_statements,
        ) = await self.data_optimizer.fetch_optimized_data(
            ticker=ticker, data_requirement=data_requirement, period_requirement=period_requirement
        )

        # Filter statements to only relevant types
        if relevant_statements and data_requirement == FinancialDataRequirement.DETAILED:
            valid_types = set(FinancialStatementType)
            drop_types = valid_types - set(relevant_statements)
            if drop_types:
                logger.info(
                    f"Filtering statements: keeping {[s.value for s in relevant_statements]}, "
                    f"dropping {[d.value for d in drop_types]}"
                )
                for stmt in annual_statements:
                    for t in drop_types:
                        stmt.pop(t, None)
                for stmt in quarterly_statements:
                    for t in drop_types:
                        stmt.pop(t, None)

        if data_requirement == FinancialDataRequirement.QUARTERLY_SUMMARY and len(quarterly_statements) == 1:
            filing_url = quarterly_statements[0].get("filing_10q_url")
            yield {
                "type": "attachment_url",
                "title": f"Quarterly 10Q report for the quarter ending on {quarterly_statements[0].get('period_end_quarter')}",
                "body": filing_url,
            }

        if data_requirement == FinancialDataRequirement.ANNUAL_SUMMARY and len(annual_statements) == 1:
            filing_url = annual_statements[0].get("filing_10k_url")
            yield {
                "type": "attachment_url",
                "title": f"Annual 10K report for the year ending {annual_statements[0].get('period_end_year')}",
                "body": filing_url,
            }

        # Fallback: If no data available and we have conversation context, answer generally
        has_no_data = (
            (not company_fundamental or not company_fundamental.get("Name"))
            and len(annual_statements) == 0
            and len(quarterly_statements) == 0
        )
        if has_no_data and conversation_messages and data_requirement != FinancialDataRequirement.NONE:
            logger.info(
                "⚠️  Fallback: No financial data available but conversation context exists. "
                "Answering question generally based on conversation context."
            )
            yield thinking_status(
                f"No {ticker.upper()} financials available — answering from conversation context",
                phase=AnalysisPhase.ANALYZE,
                step=5,
                total_steps=6,
            )

            # Use conversation context to answer generally
            company_name = company_fundamental.get("Name", "") if company_fundamental else ""
            conversation_context = format_conversation_context(
                conversation_messages, ticker or "the company", company_name
            )
            prompt = f"""{PromptComponents.current_date()}

Based on our previous conversation, answer this follow-up question:

{conversation_context}

Current question: {question}

IMPORTANT: Always respond in the same language as the CURRENT question above, regardless of the language used in previous conversation history.
Provide a helpful, general answer that builds on what we discussed before. If this is about financial strategy or concepts, explain it in general terms without requiring specific company financial data."""

            agent = MultiAgent(model_name=preferred_model)
            model_used = agent.model_name

            raw_chunks = agent.generate_content(prompt=prompt, use_google_search=use_google_search)
            for event in _process_source_tags(raw_chunks):
                yield event

            yield {"type": "model_used", "body": model_used}

            # Generate related questions
            async for related_q in self._generate_related_questions(question, preferred_model):
                yield related_q

            logger.info(
                f"Profiling CompanySpecificFinanceHandler total (fallback): {time.perf_counter() - t_start:.4f}s"
            )
            return

        yield thinking_status(
            f"Analyzing {ticker.upper()} financials...",
            phase=AnalysisPhase.ANALYZE,
            step=5,
            total_steps=6,
        )

        try:
            # Build financial context
            financial_context = self._build_financial_context(
                ticker=ticker,
                question=question,
                data_requirement=data_requirement,
                company_fundamental=company_fundamental,
                annual_statements=annual_statements,
                quarterly_statements=quarterly_statements,
                deep_analysis=deep_analysis,
                use_google_search=use_google_search,
            )

            analysis_prompt = PromptComponents.analysis_focus()
            source_prompt = PromptComponents.source_instructions()

            # Format conversation context if available
            conversation_context = ""
            if conversation_messages:
                company_name = ""
                if company_fundamental:
                    company_name = company_fundamental.get("Name", "")
                num_pairs = len(conversation_messages) // 2
                conversation_context = format_conversation_context(conversation_messages, ticker, company_name)
                conversation_context = f"\n\n{conversation_context}\n"
                logger.info(
                    f"💬 Injected {num_pairs} Q/A pair(s) of conversation context into CompanySpecificFinanceHandler prompt "
                    f"(ticker: {ticker.upper()}, company: {company_name or ticker.upper()})"
                )
            else:
                logger.debug(
                    f"💬 No conversation context to inject (CompanySpecificFinanceHandler, ticker: {ticker.upper()})"
                )

            t_model = time.perf_counter()
            agent = MultiAgent(model_name=preferred_model)
            model_used = agent.model_name

            # Combine prompts for OpenRouter (which expects a single string)
            visual_prompt = PromptComponents.visual_output_instructions()
            combined_prompt = (
                f"{financial_context}{conversation_context}\n\n{analysis_prompt}\n\n{source_prompt}\n\n{visual_prompt}"
            )

            # Enable Google Search for quarterly and annual summary questions to read filing URLs
            search_enabled = use_google_search or (
                data_requirement
                in [FinancialDataRequirement.QUARTERLY_SUMMARY, FinancialDataRequirement.ANNUAL_SUMMARY]
            )

            with langfuse.start_as_current_observation(
                as_type="generation", name="company-specific-finance-llm-call", model=model_used
            ) as gen:
                gen.update(
                    input={
                        "financial_context": financial_context,
                        "analysis_prompt": analysis_prompt,
                        "ticker": ticker,
                        "use_google_search": search_enabled,
                        "model": model_used,
                    }
                )

                first_chunk_received = False
                completion_start_time = None
                output_tokens = 0
                full_output = []

                # Build filing URL lookup once for enrichment
                filing_lookup = PromptComponents.build_filing_url_lookup(
                    ticker, annual_statements, quarterly_statements
                )

                raw_chunks = agent.generate_content(prompt=combined_prompt, use_google_search=search_enabled)
                for event in _collect_paragraph_sources(_process_source_tags(raw_chunks, filing_lookup=filing_lookup)):
                    if event["type"] == "answer":
                        text_chunk = event["body"]
                        if not first_chunk_received:
                            completion_start_time = datetime.now(timezone.utc)
                            t_first_chunk = time.perf_counter()
                            ttft = t_first_chunk - t_model
                            logger.info(f"Profiling CompanySpecificFinanceHandler time_to_first_token: {ttft:.4f}s")
                            gen.update(completion_start_time=completion_start_time)
                            first_chunk_received = True

                        full_output.append(text_chunk)
                        output_tokens += len(text_chunk.split())

                    yield event

                if not first_chunk_received:
                    yield {"type": "answer", "body": "❌ No analysis generated from the model"}

                # Update generation with output and usage
                gen.update(
                    output="".join(full_output),
                    usage_details={"output_tokens": output_tokens},
                    metadata={
                        "ticker": ticker,
                        "data_requirement": data_requirement,
                        "use_google_search": search_enabled,
                        "use_url_context": use_url_context,
                        "model": model_used,
                    },
                )

            t_model_end = time.perf_counter()
            logger.info(f"Profiling CompanySpecificFinanceHandler model_generate_content: {t_model_end - t_model:.4f}s")

            # Yield the model used for answer
            yield {"type": "model_used", "body": model_used}

            t_related = time.perf_counter()
            async for related_q in self._generate_related_questions(question, preferred_model):
                yield related_q
            t_related_end = time.perf_counter()
            logger.info(f"Profiling CompanySpecificFinanceHandler related_questions: {t_related_end - t_related:.4f}s")
            logger.info(f"Profiling CompanySpecificFinanceHandler total: {t_related_end - t_start:.4f}s")

        except Exception as e:
            logger.error(f"Error during analysis: {e}")
            yield {"type": "answer", "body": "Error during analysis. Please try again later."}

    def _build_financial_context(
        self,
        ticker: str,
        question: str,
        data_requirement: FinancialDataRequirement,
        company_fundamental: Optional[Dict[str, Any]],
        annual_statements: list[Dict[str, Any]],
        quarterly_statements: list[Dict[str, Any]],
        deep_analysis: bool = False,
        use_google_search: bool = False,
    ) -> str:
        logger.info(
            "Building financial context for analysis",
            {"ticker": ticker, "data_requirement": data_requirement},
        )

        builder = get_context_builder(data_requirement)
        return builder.build(
            ContextBuilderInput(
                ticker=ticker,
                question=question,
                company_fundamental=company_fundamental,
                annual_statements=annual_statements,
                quarterly_statements=quarterly_statements,
                deep_analysis=deep_analysis,
                use_google_search=use_google_search,
            )
        )
