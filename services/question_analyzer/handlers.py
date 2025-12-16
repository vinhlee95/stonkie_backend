"""Question handlers for different types of financial questions."""

import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Optional

from langfuse import get_client

from agent.agent import Agent
from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from ai_models.openrouter_client import OpenRouterClient
from connectors.company import CompanyConnector

from .classifier import QuestionClassifier
from .data_optimizer import FinancialDataOptimizer
from .types import FinancialDataRequirement

logger = logging.getLogger(__name__)
langfuse = get_client()
_openrouter_client: Optional[OpenRouterClient] = None


def get_openrouter_client() -> Optional[OpenRouterClient]:
    """Lazy init so we only attempt OpenRouter when configured."""
    global _openrouter_client
    if _openrouter_client is not None:
        return _openrouter_client

    try:
        _openrouter_client = OpenRouterClient()
    except Exception as e:
        logger.warning(f"OpenRouter not available: {e}")
        _openrouter_client = None
    return _openrouter_client


class BaseQuestionHandler:
    """Base class for question handlers."""

    def __init__(self, agent: Optional[Agent] = None, company_connector: Optional[CompanyConnector] = None):
        """
        Initialize the handler.

        Args:
            agent: AI agent for generating responses
            company_connector: Connector for company data
        """
        self.agent = agent or Agent(model_type="gemini")
        self.company_connector = company_connector or CompanyConnector()

    async def _generate_related_questions(self, original_question: str) -> AsyncGenerator[Dict[str, str], None]:
        """
        Generate related follow-up questions using MultiAgent with streaming.

        Buffers streaming chunks to yield complete questions one at a time.

        Args:
            original_question: The original question asked

        Yields:
            Dictionary with type "related_question" and body containing the complete question
        """
        try:
            prompt = f"""
                Based on this original question: "{original_question}"

                Generate exactly 3 high-quality follow-up questions that a curious investor might naturally ask next.

                Requirements:
                - Each question should explore a DIFFERENT dimension:
                * Question 1: Go deeper into the same topic (more specific/detailed)
                * Question 2: Compare or contrast with a related concept, company, or time period
                * Question 3: Explore a related but adjacent topic (e.g., if original was about revenue, ask about profitability or cash flow)
                - Keep questions between 8-15 words
                - Make them actionable and specific (avoid vague questions like "What else should I know?")
                - Frame questions naturally, as a user would ask them
                - Ensure questions are relevant to the original context (financial analysis, company performance, market trends)
                - Do NOT number the questions or add any prefixes
                - Put EACH question on its OWN LINE

                Output format (one question per line):
                How does Apple's gross margin compare to its competitors?
                What was the main driver behind revenue growth last quarter?
                Is the current valuation sustainable given industry trends?
            """

            agent = MultiAgent(model_name=ModelName.Gemini25FlashLite)

            # Stream complete questions one at a time
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
            logger.error(f"Error generating related questions with MultiAgent: {e}")
            # Silently fail - related questions are non-critical


class GeneralFinanceHandler(BaseQuestionHandler):
    """Handles general financial concept questions."""

    async def handle(
        self, question: str, use_google_search: bool, use_url_context: bool
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Handle general finance questions.

        Args:
            question: The question to answer
            use_google_search: Whether to use Google Search
            use_url_context: Whether to use URL context

        Yields:
            Dictionary chunks with analysis results
        """
        t_start = time.perf_counter()

        try:
            yield {"type": "thinking_status", "body": "Structuring the answer..."}

            prompt = f"""
                Please explain this financial concept or answer this question:

                {question}.

                Give a short answer in less than 150 words. 
                Break the answer into different paragraphs for better readability. 
                In the last paragraph, give an example of how this concept is used in a real-world situation
            """

            t_model = time.perf_counter()
            agent = MultiAgent()
            model_used = agent.model_name

            with langfuse.start_as_current_observation(
                as_type="generation", name="general-finance-llm-call", model=model_used
            ) as gen:
                gen.update(
                    input={
                        "prompt": prompt,
                        "use_google_search": use_google_search,
                        "model": model_used,
                    }
                )

                first_chunk_received = False
                completion_start_time = None
                output_tokens = 0
                full_output = []

                for text_chunk in agent.generate_content(prompt=prompt, use_google_search=use_google_search):
                    if not first_chunk_received:
                        completion_start_time = datetime.now(timezone.utc)
                        t_first_chunk = time.perf_counter()
                        ttft = t_first_chunk - t_model
                        logger.info(f"Profiling GeneralFinanceHandler time_to_first_token: {ttft:.4f}s")
                        gen.update(completion_start_time=completion_start_time)
                        first_chunk_received = True

                    yield {"type": "answer", "body": text_chunk}
                    full_output.append(text_chunk)
                    output_tokens += len(text_chunk.split())

                # Update generation with output and usage
                gen.update(
                    output="".join(full_output),
                    usage_details={"output_tokens": output_tokens},
                    metadata={
                        "use_google_search": use_google_search,
                        "use_url_context": use_url_context,
                        "model": model_used,
                    },
                )

            t_model_end = time.perf_counter()
            logger.info(f"Profiling GeneralFinanceHandler model_generate_content: {t_model_end - t_model:.4f}s")

            # Yield the model used for answer
            yield {"type": "model_used", "body": model_used}

            t_related = time.perf_counter()
            async for related_q in self._generate_related_questions(question):
                yield related_q
            t_related_end = time.perf_counter()
            logger.info(f"Profiling GeneralFinanceHandler related_questions: {t_related_end - t_related:.4f}s")
            logger.info(f"Profiling GeneralFinanceHandler total: {t_related_end - t_start:.4f}s")

        except Exception as e:
            logger.error(f"❌ Error generating explanation: {e}")
            yield {"type": "answer", "body": "❌ Error generating explanation. Please try again later."}


class CompanyGeneralHandler(BaseQuestionHandler):
    """Handles general questions about companies."""

    async def handle(
        self,
        ticker: str,
        question: str,
        use_google_search: bool,
        use_url_context: bool,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Handle company general questions.

        Args:
            ticker: Company ticker symbol
            question: The question to answer
            use_google_search: Whether to use Google Search
            use_url_context: Whether to use URL context

        Yields:
            Dictionary chunks with analysis results
        """
        t_start = time.perf_counter()

        company = self.company_connector.get_by_ticker(ticker)
        company_name = company.name if company else ""

        yield {
            "type": "thinking_status",
            "body": f"Analyzing general information about {company_name} (ticker: {ticker}) and preparing a concise, insightful answer...",
        }

        try:
            prompt = f"""
                You are an expert about a business. Answer the following question about {company_name} (ticker: {ticker}):
                {question}.

                Keep the response concise in under 200 words. Do not repeat points or facts. Connect the facts to a compelling story.
                Break the answer into different paragraphs and bullet points for better readability.
                
                Make sure to specify the source and source link of the answer at the end of the analysis. The format should be:
                Sources: [Source Name](Source Link), [Source Name](Source Link)
            """

            t_model = time.perf_counter()
            agent = MultiAgent()
            model_used = agent.model_name

            with langfuse.start_as_current_observation(
                as_type="generation", name="company-general-llm-call", model=model_used
            ) as gen:
                gen.update(
                    input={
                        "prompt": prompt,
                        "ticker": ticker,
                        "company_name": company_name,
                        "use_google_search": use_google_search,
                        "model": model_used,
                    }
                )

                first_chunk_received = False
                completion_start_time = None
                output_tokens = 0
                full_output = []

                for text_chunk in agent.generate_content(prompt=prompt, use_google_search=use_google_search):
                    if not first_chunk_received:
                        completion_start_time = datetime.now(timezone.utc)
                        t_first_chunk = time.perf_counter()
                        ttft = t_first_chunk - t_model
                        logger.info(f"Profiling CompanyGeneralHandler time_to_first_token: {ttft:.4f}s")
                        gen.update(completion_start_time=completion_start_time)
                        first_chunk_received = True

                    yield {"type": "answer", "body": text_chunk}
                    full_output.append(text_chunk)
                    output_tokens += len(text_chunk.split())

                # Update generation with output and usage
                gen.update(
                    output="".join(full_output),
                    usage_details={"output_tokens": output_tokens},
                    metadata={
                        "ticker": ticker,
                        "use_google_search": use_google_search,
                        "use_url_context": use_url_context,
                        "model": model_used,
                    },
                )

            t_model_end = time.perf_counter()
            logger.info(f"Profiling CompanyGeneralHandler model_generate_content: {t_model_end - t_model:.4f}s")

            # Yield the model used for answer
            yield {"type": "model_used", "body": model_used}

            t_related = time.perf_counter()
            async for related_q in self._generate_related_questions(question):
                yield related_q
            t_related_end = time.perf_counter()
            logger.info(f"Profiling CompanyGeneralHandler related_questions: {t_related_end - t_related:.4f}s")
            logger.info(f"Profiling CompanyGeneralHandler total: {t_related_end - t_start:.4f}s")

        except Exception as e:
            logger.error(f"Error generating answer: {str(e)}")
            yield {"type": "answer", "body": "❌ Error generating answer."}


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
        self, ticker: str, question: str, use_google_search: bool, use_url_context: bool
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Handle company-specific financial questions.

        Args:
            ticker: Company ticker symbol
            question: The question to answer
            use_google_search: Whether to use Google Search
            use_url_context: Whether to use URL context

        Yields:
            Dictionary chunks with analysis results
        """
        t_start = time.perf_counter()
        ticker = ticker.lower().strip()

        # Determine what financial data we need
        yield {"type": "thinking_status", "body": "Analyzing question to determine required data..."}

        data_requirement = await self.classifier.classify_data_requirement(ticker, question)
        logger.info(f"Financial data requirement: {data_requirement}")

        # Determine which specific periods are needed (if detailed data required)
        period_requirement = None
        if data_requirement == FinancialDataRequirement.DETAILED:
            yield {"type": "thinking_status", "body": "Identifying relevant financial periods..."}

            period_requirement = await self.classifier.classify_period_requirement(ticker, question)
            logger.info(f"Period requirement: {period_requirement}")

            yield {
                "type": "thinking_status",
                "body": f"Retrieving {period_requirement.period_type} financial data for analysis...",
            }

        # Fetch optimized data
        company_fundamental, annual_statements, quarterly_statements = await self.data_optimizer.fetch_optimized_data(
            ticker=ticker, data_requirement=data_requirement, period_requirement=period_requirement
        )

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
            )

            analysis_prompt = """
                Based on this financial statement, include numbers and percentages for e.g. year over year growth rates
                to answer to the question.
            """

            source_prompt = """
                Make sure to specify the source and source link of the answer at the end of the analysis. The format should be:
                Sources: [Source Name](Source Link), [Source Name](Source Link)

                If the source is from the financial statements provided in the context, no need to link, but mention clearly which statement it is from and which year or quarter it pertains to.
                MAKE SURE TO FOLLOW THIS FORMAT: 
                Sources: Annual Report 2023, Quarterly Statement Q1 2024
            """

            t_model = time.perf_counter()
            agent = MultiAgent()
            model_used = agent.model_name

            # Combine prompts for OpenRouter (which expects a single string)
            combined_prompt = f"{financial_context}\n\n{analysis_prompt}\n\n{source_prompt}"

            with langfuse.start_as_current_observation(
                as_type="generation", name="company-specific-finance-llm-call", model=model_used
            ) as gen:
                gen.update(
                    input={
                        "financial_context": financial_context,
                        "analysis_prompt": analysis_prompt,
                        "ticker": ticker,
                        "use_google_search": use_google_search,
                        "model": model_used,
                    }
                )

                first_chunk_received = False
                completion_start_time = None
                output_tokens = 0
                full_output = []

                for text_chunk in agent.generate_content(prompt=combined_prompt, use_google_search=use_google_search):
                    if not first_chunk_received:
                        completion_start_time = datetime.now(timezone.utc)
                        t_first_chunk = time.perf_counter()
                        ttft = t_first_chunk - t_model
                        logger.info(f"Profiling CompanySpecificFinanceHandler time_to_first_token: {ttft:.4f}s")
                        gen.update(completion_start_time=completion_start_time)
                        first_chunk_received = True

                    yield {
                        "type": "answer",
                        "body": text_chunk if text_chunk else "❌ No analysis generated from the model",
                    }
                    full_output.append(text_chunk if text_chunk else "")
                    output_tokens += len(text_chunk.split())

                # Update generation with output and usage
                gen.update(
                    output="".join(full_output),
                    usage_details={"output_tokens": output_tokens},
                    metadata={
                        "ticker": ticker,
                        "data_requirement": data_requirement,
                        "use_google_search": use_google_search,
                        "use_url_context": use_url_context,
                        "model": model_used,
                    },
                )

            t_model_end = time.perf_counter()
            logger.info(f"Profiling CompanySpecificFinanceHandler model_generate_content: {t_model_end - t_model:.4f}s")

            # Yield the model used for answer
            yield {"type": "model_used", "body": model_used}

            t_related = time.perf_counter()
            async for related_q in self._generate_related_questions(question):
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

        Returns:
            Formatted prompt string with financial context
        """
        base_context = f"""
            You are a seasoned financial analyst. Your task is to provide an insightful, non-repetitive analysis for the following question.

            Question: {question}
            Company: {ticker.upper()}
        """

        if data_requirement == FinancialDataRequirement.NONE:
            return f"""
                {base_context}
                
                This is a general question about {ticker.upper()} that doesn't require financial data analysis.
                Provide a clear, informative answer using your general knowledge about the company.
                Keep the response under 150 words and make it engaging.
                Use Google Search to get the most up-to-date information if needed.
            """

        elif data_requirement == FinancialDataRequirement.BASIC:
            return f"""
                {base_context}
                
                Company Fundamental Data:
                {company_fundamental}
                
                This question requires basic financial metrics. Use the fundamental data provided to answer the question.
                Focus on key metrics like market cap, P/E ratio, basic profitability, and market performance.
                Keep the response concise (under 150 words) but insightful.
                Use Google Search for additional context if needed.
            """

        else:  # DETAILED
            return f"""
                {base_context}
                
                Company Fundamental Data:
                {company_fundamental}

                Annual Financial Statements:
                {annual_statements}
                
                Quarterly Financial Statements:
                {quarterly_statements}
                
                **Instructions for your analysis:**

                1. **Summary (approx. 50 words):** Start with a concise summary of your key findings.

                2. **Detailed Analysis (approx. 100-150 words):**
                   - **Financial Performance:** Analyze key metrics from the statements (revenue, net income, profit margins)
                   - **Insightful Observations:** Explain year-over-year growth/decline and what it signifies
                   - **Industry Context & Trends:** Compare against industry peers and market trends

                **Rules:**
                - NO DUPLICATION: Each sentence should add new information
                - BE INSIGHTFUL: Provide analysis, not just data summary
                - USE SEARCH WISELY: Get up-to-date context for industry trends
                - CONCISE: Keep entire response under 200 words
                - INCLUDE SOURCES: Specify sources at the end
            """
