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
        deep_analysis: bool = False,
        preferred_model: ModelName = ModelName.Auto,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Analyze a financial question and generate insights.

        Args:
            ticker: Stock ticker symbol (e.g., 'AAPL', 'TSLA')
            question: The question to answer
            use_google_search: Whether to use Google Search for additional context
            use_url_context: Whether to use URL context
            deep_analysis: Whether to use detailed analysis prompt (default: False for shorter responses)
            preferred_model: Preferred model to use (defaults to Auto for OpenRouter Auto Router)

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

            yield {
                "type": "attachment_url",
                "body": extracted_url,
            }

            # Handle question with PDF URL
            yield {"type": "thinking_status", "body": "Analyzing PDF document from URL..."}
            async for chunk in self._handle_pdf_url_question(ticker, question, extracted_url, preferred_model):
                yield chunk
            return

        # Classify the question type using default classifier model
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
            async for chunk in handler.handle(question, use_google_search, use_url_context, preferred_model):
                yield chunk
        elif classification == QuestionType.COMPANY_GENERAL.value:
            async for chunk in handler.handle(ticker, question, use_google_search, use_url_context, preferred_model):
                yield chunk
        elif classification == QuestionType.COMPANY_SPECIFIC_FINANCE.value:
            async for chunk in handler.handle(
                ticker, question, use_google_search, use_url_context, deep_analysis, preferred_model
            ):
                yield chunk

        t_handler_end = time.perf_counter()
        logger.info(f"Profiling handler execution: {t_handler_end - t_handler:.4f}s")

        t_end = time.perf_counter()
        logger.info(f"Profiling analyze_question total: {t_end - t_start:.4f}s")

    async def _handle_pdf_url_question(
        self, ticker: str, question: str, pdf_url: str, preferred_model: ModelName = ModelName.Auto
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Handle questions that include a PDF URL.

        Args:
            ticker: Stock ticker symbol
            question: The original question containing the URL
            pdf_url: The extracted PDF URL
            preferred_model: Preferred model to use for PDF processing

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

                **Instructions for your analysis:**

                Analyze the document and organize your findings into multiple focused sections. You decide how many sections are needed to thoroughly cover the key aspects of the document that answer the user's question.

                **Structure:**
                - Start with a brief introductory paragraph (under 80 words) that directly answers the user's question
                - Follow with multiple focused sections, each covering a distinct key aspect or finding
                - Each section should have a bold, descriptive heading: **Section Heading**
                - Keep each section content under 50 words - be concise and to the point
                - Typical number of sections: 4-8 depending on document complexity and question scope

                **Section Guidelines:**
                - Each section heading should be specific, descriptive, and catchy (3-5 words max). The section headings must be in separate lines and bolded.
                - Each section content should focus on ONE key finding or aspect
                - Include specific numbers and metrics where relevant
                - Highlight significant changes, trends, or anomalies
                - Bold important figures and percentages
                - Prioritize the most relevant information that directly answers the user's question
                - Use the largest appropriate unit for numbers (e.g., "$1.5 billion" not "$1,500 million")
                - Make every word count - avoid filler phrases

                **Example Structure:**

                [Brief intro answering the question - under 50 words]

                **Section Heading 1**

                [Concise finding with key metrics - under 50 words]

                **Section Heading 2**

                [Another focused insight - under 50 words]

                [Continue with additional sections as needed...]

                **Sources:**
                At the end, cite your sources: "Sources: Document pages X-Y" or "Sources: [Section name from document]"

                Answer in a professional, informative tone. Prioritize clarity and scannability over narrative flow.
            """.strip()

            # Initialize MultiAgent for OpenRouter PDF processing with preferred model
            agent = MultiAgent(model_name=preferred_model)

            # Stream response from AI model
            try:
                for chunk in agent.generate_content_with_pdf_url(
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

            yield {"type": "model_used", "body": agent.model_name}

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
