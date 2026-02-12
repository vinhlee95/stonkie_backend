"""Company-specific financial analysis handler."""

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from langfuse import get_client

from agent.agent import Agent
from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.company import CompanyConnector
from utils.conversation_format import format_conversation_context

from .classifier import QuestionClassifier
from .context_builders import ContextBuilderInput, get_context_builder, validate_section_titles
from .context_builders.components import PromptComponents
from .data_optimizer import FinancialDataOptimizer
from .handlers import BaseQuestionHandler, _collect_paragraph_sources, _process_source_tags
from .types import FinancialDataRequirement

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

    async def _analyze_question_dimensions(self, question: str, ticker: str) -> Optional[List[Dict]]:
        """
        Analyze the question and generate relevant section titles with focus points.

        Args:
            question: The financial question being asked
            ticker: Company ticker symbol

        Returns:
            List of section dictionaries with 'title' and 'focus_points', or None if failed
        """
        try:
            prompt = f"""
                You are an expert financial analyst. Analyze this question about {ticker.upper()} and determine the most relevant sections for a comprehensive answer.

                Question: {question}

                Identify the 2 most important aspects to cover. These will form the main body of the analysis (a summary section will be added automatically).

                Return ONLY a JSON object (no markdown, no explanation) with this exact structure:
                {{
                    "sections": [
                        {{
                            "title": "Catchy Section Title (max 6 words)",
                            "focus_points": [
                                "Specific aspect to analyze",
                                "Metrics or data points to examine",
                                "Comparisons or context to provide"
                            ]
                        }}
                    ]
                }}

                **Examples:**

                Question: "How is Apple's revenue growing?"
                {{
                    "sections": [
                        {{
                            "title": "Revenue Growth Trajectory",
                            "focus_points": [
                                "Analyze revenue growth rates year-over-year",
                                "Identify key product lines driving growth",
                                "Compare against industry peers"
                            ]
                        }},
                        {{
                            "title": "Growth Sustainability & Outlook",
                            "focus_points": [
                                "Assess growth consistency and patterns",
                                "Evaluate market opportunities and risks",
                                "Project future growth potential"
                            ]
                        }}
                    ]
                }}

                Question: "What is Tesla's profit margin?"
                {{
                    "sections": [
                        {{
                            "title": "Profitability Metrics",
                            "focus_points": [
                                "Calculate gross and net profit margins",
                                "Analyze margin trends over recent periods"
                            ]
                        }},
                        {{
                            "title": "Competitive Comparison",
                            "focus_points": [
                                "Compare with automotive industry benchmarks",
                                "Assess margin sustainability and risks"
                            ]
                        }}
                    ]
                }}

                Question: "How is chip business competition going on?"
                {{
                    "sections": [
                        {{
                            "title": "Competitive Market Position",
                            "focus_points": [
                                "Identify major competitors and market share",
                                "Analyze competitive advantages and weaknesses",
                                "Assess pricing power and differentiation"
                            ]
                        }},
                        {{
                            "title": "Market Dynamics & Outlook",
                            "focus_points": [
                                "Evaluate demand trends and growth drivers",
                                "Examine supply chain constraints and opportunities",
                                "Project future competitive landscape"
                            ]
                        }}
                    ]
                }}

                **Guidelines:**
                - Generate EXACTLY 2 sections (the most relevant aspects)
                - Section titles must be catchy, professional, and max 6 words
                - Each section should have 2-4 focus points
                - Focus points should be specific and actionable
                - Return ONLY valid JSON, no markdown code blocks
            """

            agent = MultiAgent(model_name=ModelName.Gemini30Flash)

            # Collect full response with timeout
            response_chunks = []

            async def collect_response():
                for chunk in agent.generate_content(prompt=prompt, use_google_search=False):
                    response_chunks.append(chunk)

            await asyncio.wait_for(collect_response(), timeout=3.0)
            full_response = "".join(response_chunks)

            # Parse and validate JSON
            parsed_data = self._parse_dimension_json(full_response)
            if not parsed_data:
                return None

            sections = parsed_data.get("sections", [])
            if not validate_section_titles(sections):
                return None

            return sections

        except asyncio.TimeoutError:
            logger.error(f"Timeout generating dimension sections for question: {question}")
            return None
        except Exception as e:
            logger.error(f"Error generating dimension sections: {e}")
            return None

    def _parse_dimension_json(self, response: str) -> Optional[Dict]:
        """
        Parse JSON from AI response, handling markdown code blocks.

        Args:
            response: Raw response from AI model

        Returns:
            Parsed dictionary or None if parsing failed
        """
        try:
            # Try direct JSON parsing first
            return json.loads(response)
        except json.JSONDecodeError:
            # Try extracting from markdown code block
            try:
                match = re.search(r"```(?:json)?\s*({.*?})\s*```", response, re.DOTALL)
                if match:
                    return json.loads(match.group(1))

                # Try finding any JSON object in the response
                match = re.search(r"{.*}", response, re.DOTALL)
                if match:
                    return json.loads(match.group(0))

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse dimension JSON from response: {e}")

        logger.error(f"Could not extract valid JSON from response: {response[:200]}...")
        return None

    async def handle(
        self,
        ticker: str,
        question: str,
        use_google_search: bool,
        use_url_context: bool,
        deep_analysis: bool = False,
        preferred_model: ModelName = ModelName.Auto,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
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
            yield {"type": "thinking_status", "body": "Answering based on our previous discussion..."}

            # Use conversation context to answer generally
            company_name = ""
            conversation_context = format_conversation_context(
                conversation_messages, ticker or "the company", company_name
            )
            prompt = f"""Based on our previous conversation, answer this follow-up question:

{conversation_context}

Current question: {question}

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

        # Determine what financial data we need
        yield {"type": "thinking_status", "body": "Analyzing question to determine required data..."}

        # Use default classifier model (Gemini3.0 Flash) for classification
        data_requirement = await self.classifier.classify_data_requirement(ticker, question)
        logger.info(f"Financial data requirement: {data_requirement}")

        # Determine which specific periods are needed (if detailed data required)
        period_requirement = None
        if data_requirement in [
            FinancialDataRequirement.DETAILED,
            FinancialDataRequirement.QUARTERLY_SUMMARY,
            FinancialDataRequirement.ANNUAL_SUMMARY,
        ]:
            yield {"type": "thinking_status", "body": "Identifying relevant financial periods..."}

            period_requirement = await self.classifier.classify_period_requirement(ticker, question)
            logger.info(f"Period requirement: {period_requirement}")

            yield {
                "type": "thinking_status",
                "body": f"Retrieving {period_requirement.period_type} financial data for analysis...",
            }

        # Run dimension analysis in parallel with data fetching (for detailed analysis)
        dimension_sections = None
        if data_requirement == FinancialDataRequirement.DETAILED:
            yield {"type": "thinking_status", "body": "Determining key analysis dimensions..."}

            # Execute in parallel to minimize latency
            results = await asyncio.gather(
                self._analyze_question_dimensions(question, ticker),
                self.data_optimizer.fetch_optimized_data(
                    ticker=ticker, data_requirement=data_requirement, period_requirement=period_requirement
                ),
                return_exceptions=True,
            )

            # Extract results and handle exceptions
            dimension_result = results[0]
            data_result = results[1]

            if isinstance(dimension_result, BaseException):
                logger.error(f"Failed to generate dimension sections: {dimension_result}")
                dimension_sections = None
            else:
                dimension_sections = dimension_result

            if isinstance(data_result, BaseException):
                logger.error(f"Failed to fetch financial data: {data_result}")
                raise data_result
            else:
                company_fundamental, annual_statements, quarterly_statements = data_result

        # For non-detailed queries (QUARTERLY_SUMMARY, BASIC, NONE), fetch data
        if data_requirement != FinancialDataRequirement.DETAILED:
            (
                company_fundamental,
                annual_statements,
                quarterly_statements,
            ) = await self.data_optimizer.fetch_optimized_data(
                ticker=ticker, data_requirement=data_requirement, period_requirement=period_requirement
            )

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
            (not company_fundamental or not company_fundamental.get("name"))
            and len(annual_statements) == 0
            and len(quarterly_statements) == 0
        )
        if has_no_data and conversation_messages:
            logger.info(
                "⚠️  Fallback: No financial data available but conversation context exists. "
                "Answering question generally based on conversation context."
            )
            yield {"type": "thinking_status", "body": "Answering based on our previous discussion..."}

            # Use conversation context to answer generally
            company_name = company_fundamental.get("name", "") if company_fundamental else ""
            conversation_context = format_conversation_context(
                conversation_messages, ticker or "the company", company_name
            )
            prompt = f"""Based on our previous conversation, answer this follow-up question:

{conversation_context}

Current question: {question}

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

        yield {"type": "thinking_status", "body": "Analyzing data and preparing insights..."}

        try:
            # Build financial context
            financial_context = self._build_financial_context(
                ticker=ticker,
                question=question,
                data_requirement=data_requirement,
                company_fundamental=company_fundamental,
                annual_statements=annual_statements,
                quarterly_statements=quarterly_statements,
                dimension_sections=dimension_sections,
                deep_analysis=deep_analysis,
            )

            analysis_prompt = PromptComponents.analysis_focus()
            source_prompt = PromptComponents.source_instructions()

            # Format conversation context if available
            conversation_context = ""
            if conversation_messages:
                company_name = ""
                if company_fundamental:
                    company_name = company_fundamental.get("name", "")
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
            combined_prompt = f"{financial_context}{conversation_context}\n\n{analysis_prompt}\n\n{source_prompt}"

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
        dimension_sections: Optional[List[Dict]] = None,
        deep_analysis: bool = False,
    ) -> str:
        """
        Build the appropriate financial context prompt based on data requirement level.

        Args:
            ticker: Company ticker symbol
            question: The question being asked
            data_requirement: Level of data required
            company_fundamental: Fundamental company data
            annual_statements: Annual financial statements
            quarterly_statements: Quarterly financial statements
            dimension_sections: AI-generated section structure with titles and focus points
            deep_analysis: Whether to use detailed analysis prompt (default: False for shorter responses)

        Returns:
            Formatted prompt string with financial context
        """
        logger.info(
            "Building financial context for analysis",
            {"ticker": ticker, "data_requirement": data_requirement, "dimension_sections": dimension_sections},
        )

        builder = get_context_builder(data_requirement)
        return builder.build(
            ContextBuilderInput(
                ticker=ticker,
                question=question,
                company_fundamental=company_fundamental,
                annual_statements=annual_statements,
                quarterly_statements=quarterly_statements,
                dimension_sections=dimension_sections,
                deep_analysis=deep_analysis,
            )
        )
