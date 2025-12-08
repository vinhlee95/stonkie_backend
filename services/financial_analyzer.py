"""Financial analyzer service - main entry point for question analysis."""

import logging
import time
from typing import Any, AsyncGenerator, Dict, Optional

from agent.agent import Agent
from connectors.company import CompanyConnector
from connectors.company_financial import CompanyFinancialConnector
from services.question_analyzer.classifier import QuestionClassifier
from services.question_analyzer.data_optimizer import FinancialDataOptimizer
from services.question_analyzer.handlers import (
    CompanyGeneralHandler,
    CompanySpecificFinanceHandler,
    GeneralFinanceHandler,
)
from services.question_analyzer.types import QuestionType

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
        self.classifier = QuestionClassifier(self.agent)
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

        # Classify the question type
        classification = await self.classifier.classify_question_type(question)
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
