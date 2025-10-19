"""Question handlers for different types of financial questions."""

import logging
import time
from typing import AsyncGenerator, Dict, Any, Optional

from agent.agent import Agent
from ai_models.gemini import ContentType
from ai_models.model_name import ModelName
from connectors.company import CompanyConnector
from .types import FinancialDataRequirement
from .data_optimizer import FinancialDataOptimizer
from .classifier import QuestionClassifier

logger = logging.getLogger(__name__)


class BaseQuestionHandler:
    """Base class for question handlers."""
    
    def __init__(
        self,
        agent: Optional[Agent] = None,
        company_connector: Optional[CompanyConnector] = None
    ):
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
        Generate related follow-up questions.
        
        Args:
            original_question: The original question asked
            
        Yields:
            Dictionary with type "related_question" and body containing the question
        """
        prompt = f"""
            Based on this original question: "{original_question}"
            Generate 3 related but different follow-up questions that users might want to ask next.
            Make sure related questions are short and concise. Ideally, less than 15 words each.
            Return only the questions, do not return the number or order of the question.
        """
        response = self.agent.generate_content_and_normalize_results(
            [prompt], 
            model_name=ModelName.Gemini25FlashLite
        )
        async for answer in response:
            yield {
                "type": "related_question",
                "body": answer
            }


class GeneralFinanceHandler(BaseQuestionHandler):
    """Handles general financial concept questions."""
    
    async def handle(
        self,
        question: str,
        use_google_search: bool,
        use_url_context: bool
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
            yield {
                "type": "thinking_status",
                "body": "Structuring the answer..."
            }
            
            t_model = time.perf_counter()
            for part in self.agent.generate_content(
                prompt=f"""
                    Please explain this financial concept or answer this question:

                    {question}.

                    Give a short answer in less than 150 words. 
                    Break the answer into different paragraphs for better readability. 
                    In the last paragraph, give an example of how this concept is used in a real-world situation
                """,
                model_name=ModelName.Gemini25FlashLite,
                stream=True,
                use_google_search=use_google_search,
                use_url_context=use_url_context,
            ):
                yield {
                    "type": "answer",
                    "body": part.text
                }
            t_model_end = time.perf_counter()
            logger.info(f"Profiling GeneralFinanceHandler model_generate_content: {t_model_end - t_model:.4f}s")

            t_related = time.perf_counter()
            async for related_q in self._generate_related_questions(question):
                yield related_q
            t_related_end = time.perf_counter()
            logger.info(f"Profiling GeneralFinanceHandler related_questions: {t_related_end - t_related:.4f}s")
            logger.info(f"Profiling GeneralFinanceHandler total: {t_related_end - t_start:.4f}s")
            
        except Exception as e:
            logger.error(f"❌ Error generating explanation: {e}")
            yield {
                "type": "answer",
                "body": "❌ Error generating explanation. Please try again later."
            }


class CompanyGeneralHandler(BaseQuestionHandler):
    """Handles general questions about companies."""
    
    async def handle(
        self,
        ticker: str,
        question: str,
        use_google_search: bool,
        use_url_context: bool
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
            "body": f"Analyzing general information about {company_name} (ticker: {ticker}) and preparing a concise, insightful answer..."
        }

        try:
            prompt = f"""
                You are an expert about a business. Answer the following question about {company_name} (ticker: {ticker}):
                {question}.

                Keep the response concise in under 150 words. Do not repeat points or facts. Connect the facts to a compelling story.
                Break the answer into different paragraphs and bullet points for better readability.
                Make sure to specify the source of the answer at the end of the analysis.
            """
            
            t_model = time.perf_counter()
            for part in self.agent.generate_content(
                prompt=prompt, 
                model_name=ModelName.GeminiFlash, 
                stream=True,
                thought=True,
                use_google_search=use_google_search,
                use_url_context=use_url_context,
            ):
                if part.type == ContentType.Thought:
                    yield {
                        "type": "thinking_status",
                        "body": part.text
                    }
                elif part.type == ContentType.Answer:
                    yield {
                        "type": "answer",
                        "body": part.text
                    }
                elif part.type == ContentType.Ground:
                    yield {
                        "type": "google_search_ground",
                        "body": part.ground.text if part.ground else "",
                        "url": part.ground.uri if part.ground else ""
                    }
                else:
                    logger.warning(f"Unknown content part {str(part)}")
            t_model_end = time.perf_counter()
            logger.info(f"Profiling CompanyGeneralHandler model_generate_content: {t_model_end - t_model:.4f}s")

            t_related = time.perf_counter()
            async for related_q in self._generate_related_questions(question):
                yield related_q
            t_related_end = time.perf_counter()
            logger.info(f"Profiling CompanyGeneralHandler related_questions: {t_related_end - t_related:.4f}s")
            logger.info(f"Profiling CompanyGeneralHandler total: {t_related_end - t_start:.4f}s")
            
        except Exception as e:
            logger.error(f"Error generating answer: {str(e)}")
            yield {
                "type": "answer",
                "body": f"❌ Error generating answer."
            }


class CompanySpecificFinanceHandler(BaseQuestionHandler):
    """Handles company-specific financial analysis questions."""
    
    def __init__(
        self,
        agent: Optional[Agent] = None,
        company_connector: Optional[CompanyConnector] = None,
        data_optimizer: Optional[FinancialDataOptimizer] = None,
        classifier: Optional[QuestionClassifier] = None
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
        self.classifier = classifier or QuestionClassifier(agent)
    
    async def handle(
        self,
        ticker: str,
        question: str,
        use_google_search: bool,
        use_url_context: bool
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
        yield {
            "type": "thinking_status",
            "body": "Analyzing question to determine required data..."
        }
        
        data_requirement = await self.classifier.classify_data_requirement(ticker, question)
        logger.info(f"Financial data requirement: {data_requirement}")

        # Determine which specific periods are needed (if detailed data required)
        period_requirement = None
        if data_requirement == FinancialDataRequirement.DETAILED:
            yield {
                "type": "thinking_status",
                "body": "Identifying relevant financial periods..."
            }
            
            period_requirement = await self.classifier.classify_period_requirement(ticker, question)
            logger.info(f"Period requirement: {period_requirement}")
            
            yield {
                "type": "thinking_status",
                "body": f"Retrieving {period_requirement.period_type} financial data for analysis..."
            }

        # Fetch optimized data
        company_fundamental, annual_statements, quarterly_statements = await self.data_optimizer.fetch_optimized_data(
            ticker=ticker,
            data_requirement=data_requirement,
            period_requirement=period_requirement
        )

        yield {
            "type": "thinking_status", 
            "body": "Analyzing data and preparing insights..."
        }

        try:
            # Build financial context
            financial_context = self._build_financial_context(
                ticker=ticker,
                question=question,
                data_requirement=data_requirement,
                company_fundamental=company_fundamental,
                annual_statements=annual_statements,
                quarterly_statements=quarterly_statements
            )

            analysis_prompt = """
                Based on this financial statement, include numbers and percentages for e.g. year over year growth rates
                to answer to the question.
            """

            t_model = time.perf_counter()
            for part in self.agent.generate_content(
                [financial_context, analysis_prompt], 
                model_name=ModelName.GeminiFlash, 
                stream=True, 
                thought=True,
                use_google_search=use_google_search,
                use_url_context=use_url_context,
            ):
                if part.type == ContentType.Thought:
                    yield {
                        "type": "thinking_status",
                        "body": part.text
                    }
                elif part.type == ContentType.Answer:
                    yield {
                        "type": "answer",
                        "body": part.text if part.text else "❌ No analysis generated from the model"
                    }
                elif part.type == ContentType.Ground:
                    yield {
                        "type": "google_search_ground",
                        "body": part.ground.text if part.ground else "",
                        "url": part.ground.uri if part.ground else ""
                    }
                else:
                    logger.warning(f'Unknown content part {str(part)}')
            
            t_model_end = time.perf_counter()
            logger.info(f"Profiling CompanySpecificFinanceHandler model_generate_content: {t_model_end - t_model:.4f}s")

            t_related = time.perf_counter()
            async for related_q in self._generate_related_questions(question):
                yield related_q
            t_related_end = time.perf_counter()
            logger.info(f"Profiling CompanySpecificFinanceHandler related_questions: {t_related_end - t_related:.4f}s")
            logger.info(f"Profiling CompanySpecificFinanceHandler total: {t_related_end - t_start:.4f}s")

        except Exception as e:
            logger.error(f"Error during analysis: {e}")
            yield {
                "type": "answer",
                "body": "Error during analysis. Please try again later."
            }
    
    def _build_financial_context(
        self,
        ticker: str,
        question: str,
        data_requirement: FinancialDataRequirement,
        company_fundamental: Optional[Dict[str, Any]],
        annual_statements: list[Dict[str, Any]],
        quarterly_statements: list[Dict[str, Any]]
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
