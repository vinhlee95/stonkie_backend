"""Financial analyzer service - main entry point for question analysis."""

import logging
import time
from typing import Any, AsyncGenerator, Dict, Optional

from langfuse import observe

from agent.agent import Agent
from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.company import CompanyConnector
from connectors.company_financial import CompanyFinancialConnector
from services.question_analyzer import CompanySpecificFinanceHandler
from services.question_analyzer.classifier import QuestionClassifier
from services.question_analyzer.data_optimizer import FinancialDataOptimizer
from services.question_analyzer.handlers import (
    CompanyGeneralHandler,
    GeneralFinanceHandler,
)
from services.question_analyzer.types import QuestionType
from utils.url_helper import extract_first_url, strip_url_from_text, validate_pdf_url

logger = logging.getLogger(__name__)


class FinancialAnalyzer:
    """Main service for analyzing financial questions and generating insights."""

    def __init__(
        self,
        agent: Optional[Agent] = None,
        company_connector: Optional[CompanyConnector] = None,
        company_financial_connector: Optional[CompanyFinancialConnector] = None,
    ):
        """
        Initialize the financial analyzer.

        Args:
            agent: AI agent for analysis
            company_connector: Connector for company data
            company_financial_connector: Connector for financial data
        """
        self.agent = agent or Agent(model_type="gemini")
        self.company_connector = company_connector or CompanyConnector()
        self.company_financial_connector = company_financial_connector or CompanyFinancialConnector()

        # Initialize components
        self.classifier = QuestionClassifier()
        self.data_optimizer = FinancialDataOptimizer(self.company_financial_connector)

        # Initialize handlers
        self.handlers = {
            QuestionType.GENERAL_FINANCE.value: GeneralFinanceHandler(
                agent=self.agent, company_connector=self.company_connector
            ),
            QuestionType.COMPANY_GENERAL.value: CompanyGeneralHandler(
                agent=self.agent, company_connector=self.company_connector
            ),
            QuestionType.COMPANY_SPECIFIC_FINANCE.value: CompanySpecificFinanceHandler(
                agent=self.agent,
                company_connector=self.company_connector,
                data_optimizer=self.data_optimizer,
                classifier=self.classifier,
            ),
        }

    @observe(name="financial_analyzer.analyze_question")
    async def analyze_question(
        self,
        ticker: str,
        question: str,
        use_google_search: bool = False,
        use_url_context: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Analyze a financial question and generate insights.

        Args:
            ticker: Stock ticker symbol (e.g., 'AAPL', 'TSLA')
            question: The question to answer
            use_google_search: Whether to use Google Search for additional context
            use_url_context: Whether to use URL context

        Yields:
            Dictionary chunks with analysis results containing:
                - type: "thinking_status", "answer", "related_question", "google_search_ground"
                - body: The content
                - url: (optional) URL for google_search_ground type
        """
        t_start = time.perf_counter()

        yield {"type": "thinking_status", "body": "Just a moment..."}

        # Check for PDF URL in question
        extracted_url = extract_first_url(question)
        if extracted_url:
            # Validate if URL points to a PDF
            is_valid, error_message = validate_pdf_url(extracted_url)

            if not is_valid:
                yield {"type": "answer", "body": f"❌ {error_message}"}
                return

            # Handle question with PDF URL
            yield {"type": "thinking_status", "body": "Analyzing PDF document from URL..."}
            async for chunk in self._handle_pdf_url_question(ticker, question, extracted_url):
                yield chunk
            return

        # Classify the question type
        classification = await self.classifier.classify_question_type(question, ticker)
        logger.info(f"Question classified as: {classification}")

        if not classification:
            yield {"type": "answer", "body": "❌ Unable to classify question type"}
            return

        if use_google_search:
            yield {
                "type": "thinking_status",
                "body": "Using Google Search to get up-to-date information. This might take a bit longer, but it will help you get a better answer.",
            }

        # Get the appropriate handler
        handler = self.handlers.get(classification)
        if not handler:
            yield {"type": "answer", "body": "❌ Unable to find handler for question type"}
            return

        # Route to the appropriate handler
        t_handler = time.perf_counter()

        if classification == QuestionType.GENERAL_FINANCE.value:
            async for chunk in handler.handle(question, use_google_search, use_url_context):
                yield chunk
        elif classification == QuestionType.COMPANY_GENERAL.value:
            async for chunk in handler.handle(ticker, question, use_google_search, use_url_context):
                yield chunk
        elif classification == QuestionType.COMPANY_SPECIFIC_FINANCE.value:
            async for chunk in handler.handle(ticker, question, use_google_search, use_url_context):
                yield chunk

        t_handler_end = time.perf_counter()
        logger.info(f"Profiling handler execution: {t_handler_end - t_handler:.4f}s")

        t_end = time.perf_counter()
        logger.info(f"Profiling analyze_question total: {t_end - t_start:.4f}s")

    async def _handle_pdf_url_question(
        self, ticker: str, question: str, pdf_url: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Handle questions that include a PDF URL.

        Args:
            ticker: Stock ticker symbol
            question: The original question containing the URL
            pdf_url: The extracted PDF URL

        Yields:
            Dictionary chunks with analysis results
        """
        try:
            # Strip URL from question to get clean question text
            clean_question = strip_url_from_text(question, pdf_url)

            # Get company name for context
            company_data = self.company_connector.get_fundamental_data(ticker)
            company_name = company_data.name if company_data else ticker.upper()

            # Build prompt with company context
            prompt = f"""
                You are a financial analyst assistant analyzing a document for {company_name} ({ticker.upper()}).

                User Question: {clean_question}

                Please analyze the provided PDF document and answer the question comprehensively. Focus on:
                1. Directly answering the user's specific question
                2. Providing relevant financial data and metrics from the document
                3. Offering insights and analysis based on the document content
                4. Being clear and concise in your response

                Answer in a professional, informative tone suitable for financial analysis.
            """.strip()

            # Initialize MultiAgent for OpenRouter PDF processing
            multi_agent = MultiAgent(model_name=ModelName.Gemini30Flash)

            # Stream response from AI model
            try:
                for chunk in multi_agent.generate_content_with_pdf_url(
                    prompt=prompt,
                    pdf_url=pdf_url,
                    filename=f"{ticker.lower()}_document.pdf",
                    pdf_engine="pdf-text",
                ):
                    yield {"type": "answer", "body": chunk}
            except Exception as e:
                logger.error(f"Error generating content with PDF URL: {e}")
                yield {
                    "type": "answer",
                    "body": "❌ Error processing PDF document. The file may be inaccessible or too large.",
                }
                return

        except Exception as e:
            logger.error(f"Error handling PDF URL question: {e}")
            yield {"type": "answer", "body": "❌ Error analyzing document. Please try again later."}


# Factory function for backwards compatibility
def create_financial_analyzer(
    agent: Optional[Agent] = None,
    company_connector: Optional[CompanyConnector] = None,
    company_financial_connector: Optional[CompanyFinancialConnector] = None,
) -> FinancialAnalyzer:
    """
    Create a FinancialAnalyzer instance.

    Args:
        agent: AI agent for analysis
        company_connector: Connector for company data
        company_financial_connector: Connector for financial data

    Returns:
        Configured FinancialAnalyzer instance
    """
    return FinancialAnalyzer(
        agent=agent, company_connector=company_connector, company_financial_connector=company_financial_connector
    )
