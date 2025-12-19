"""Question handlers for different types of financial questions."""

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from langfuse import get_client, observe

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

    @observe(name="generate_related_questions")
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

            agent = MultiAgent(model_name=ModelName.Gemini25FlashLite)

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
            if not self._validate_section_titles(sections):
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

    def _validate_section_titles(self, sections: List[Dict]) -> bool:
        """
        Validate section structure and titles.

        Args:
            sections: List of section dictionaries

        Returns:
            True if valid, False otherwise
        """
        if not sections or not isinstance(sections, list):
            logger.error("Sections is not a valid list")
            return False

        if len(sections) != 2:
            logger.error(f"Invalid number of sections: {len(sections)} (must be exactly 2)")
            return False

        for i, section in enumerate(sections):
            # Check required keys
            if "title" not in section or "focus_points" not in section:
                logger.error(f"Section {i} missing required keys: {section}")
                return False

            title = section["title"]
            focus_points = section["focus_points"]

            # Validate title
            if not isinstance(title, str) or not title.strip():
                logger.error(f"Section {i} has invalid title: {title}")
                return False

            # Check word count (max 6 words)
            word_count = len(title.split())
            if word_count > 6:
                logger.error(f"Section {i} title too long ({word_count} words): {title}")
                return False

            # Check for special characters (allow letters, numbers, spaces, &, -)
            if re.search(r"[^a-zA-Z0-9\s&-]", title):
                logger.error(f"Section {i} title contains special characters: {title}")
                return False

            # Validate focus points
            if not isinstance(focus_points, list) or len(focus_points) == 0:
                logger.error(f"Section {i} has invalid focus_points: {focus_points}")
                return False

            for j, point in enumerate(focus_points):
                if not isinstance(point, str) or not point.strip():
                    logger.error(f"Section {i} focus_point {j} is invalid: {point}")
                    return False

        return True

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

            if isinstance(dimension_result, Exception):
                logger.error(f"Failed to generate dimension sections: {dimension_result}")
                dimension_sections = None
            else:
                dimension_sections = dimension_result

            if isinstance(data_result, Exception):
                logger.error(f"Failed to fetch financial data: {data_result}")
                raise data_result
            else:
                company_fundamental, annual_statements, quarterly_statements = data_result
        else:
            # For non-detailed queries, just fetch data
            (
                company_fundamental,
                annual_statements,
                quarterly_statements,
            ) = await self.data_optimizer.fetch_optimized_data(
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
                dimension_sections=dimension_sections,
            )

            analysis_prompt = """
                Focus on analytical reasoning and interpretation. Use select key numbers to support your analysis,
                but prioritize explaining WHY trends exist and WHAT drives the financial performance.
                Include a few specific figures where they strengthen your argument, but avoid listing exhaustive metrics.
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
        dimension_sections: Optional[List[Dict]] = None,
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

        Returns:
            Formatted prompt string with financial context
        """
        base_context = f"""
            You are a seasoned financial analyst. Your task is to provide an insightful, non-repetitive analysis for the following question.

            Question: {question}
            Company: {ticker.upper()}
        """

        logger.info(
            "Building financial context for analysis",
            {"ticker": ticker, "data_requirement": data_requirement, "dimension_sections": dimension_sections},
        )

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
            # Use AI-generated sections or fallback to default structure
            sections = dimension_sections
            if not sections or not self._validate_section_titles(sections):
                logger.info("Using fallback section structure")
                sections = [
                    {
                        "title": "Financial Performance",
                        "focus_points": [
                            "Analyze key metrics from the statements (revenue, net income, profit margins)",
                            "Explain year-over-year growth/decline trends and patterns",
                        ],
                    },
                    {
                        "title": "Strategic Positioning",
                        "focus_points": [
                            "Industry context and competitive position",
                            "Future outlook, opportunities, and growth risks",
                        ],
                    },
                ]

            # Word allocation: 80 words for summary, 160 words each for 2 main sections (total: 400)
            summary_words = 80
            section_words = 160

            # Build dynamic section instructions for the 2 main sections
            sections_text = ""
            for i, section in enumerate(sections, 1):
                sections_text += f"\n**{section['title']}**\n\n"
                sections_text += f"(~{section_words} words) Focus on:\n"
                for point in section["focus_points"]:
                    sections_text += f"- {point}\n"
                sections_text += "\n"

            return f"""
                {base_context}
                
                Company Fundamental Data:
                {company_fundamental}

                Annual Financial Statements:
                {annual_statements}
                
                Quarterly Financial Statements:
                {quarterly_statements}
                
                **Instructions for your analysis:**

                Structure your response with EXACTLY 3 sections in this order:
                
                (~{summary_words} words) Provide a concise overview that previews the key findings from the two sections below. Highlight the most important takeaway.

                {sections_text}

                **Formatting Guidelines:**
                - Start each section with its title in markdown bold: **Section Title**
                - Add a blank line after the title before starting the paragraph
                - Each section should be a cohesive paragraph (or 2-3 short paragraphs)
                - Use numbers strategically - select 2-4 key figures per section that best support your analysis
                - Keep total response under 300 words
                
                **Analysis Rules:**
                - PRIORITIZE REASONING: Explain WHY trends occur, WHAT drives the changes, and WHAT it means for the business
                - STRATEGIC USE OF NUMBERS: Include specific figures only when they strengthen your argument or illustrate a key point
                - IDENTIFY DRIVERS: Explain the underlying business factors, market conditions, or strategic decisions behind the numbers
                - CONNECT THE DOTS: Link financial performance to business strategy, competitive position, and market dynamics
                - NO DUPLICATION: Each sentence should add new information
                - USE SEARCH WISELY: Get up-to-date context for industry trends and competitive landscape
                
                **Sources:**
                At the end, clearly specify your sources in this format:
                - If from financial statements: "Sources: Annual Report 2023, Quarterly Statement Q1 2024"
                - If from search: "Sources: [Source Name](Source Link), [Source Name](Source Link)"
            """
