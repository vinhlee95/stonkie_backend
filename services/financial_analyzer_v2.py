"""Financial analyzer v2 service — dispatches all 4 stock QuestionType values."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.company import CompanyConnector
from connectors.company_financial import CompanyFinancialConnector
from services.analysis_progress import AnalysisPhase, thinking_status
from services.question_analyzer.classifier import QuestionClassifier
from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2
from services.question_analyzer.handlers_v2 import (
    CompanyGeneralHandlerV2,
    CompanySpecificFinanceHandlerV2,
    GeneralFinanceHandlerV2,
)
from services.question_analyzer.types import QuestionType
from services.search_decision_engine import SearchDecisionEngine
from utils.url_helper import extract_first_url, is_sec_filing_url, strip_url_from_text, validate_pdf_url

logger = logging.getLogger(__name__)


class FinancialAnalyzerV2:
    """Main v2 analyzer; dispatches by classifier output to v2 handlers."""

    def __init__(
        self,
        classifier: Optional[QuestionClassifier] = None,
        search_decision_engine: Optional[SearchDecisionEngine] = None,
        company_connector: Optional[CompanyConnector] = None,
        company_financial_connector: Optional[CompanyFinancialConnector] = None,
        company_general_handler: Optional[CompanyGeneralHandlerV2] = None,
        general_finance_handler: Optional[GeneralFinanceHandlerV2] = None,
        company_specific_finance_handler: Optional[CompanySpecificFinanceHandlerV2] = None,
        comparison_handler: Optional[CompanyComparisonHandlerV2] = None,
    ):
        self.classifier = classifier or QuestionClassifier()
        self.search_decision_engine = search_decision_engine or SearchDecisionEngine()
        self.company_connector = company_connector or CompanyConnector()
        self.company_financial_connector = company_financial_connector or CompanyFinancialConnector()
        self.company_general_handler = company_general_handler or CompanyGeneralHandlerV2()
        self.general_finance_handler = general_finance_handler or GeneralFinanceHandlerV2()
        self.company_specific_finance_handler = company_specific_finance_handler or CompanySpecificFinanceHandlerV2()
        self.comparison_handler = comparison_handler or CompanyComparisonHandlerV2()

    async def _handle_pdf_url_question(
        self,
        ticker: str,
        question: str,
        pdf_url: str,
        preferred_model: ModelName = ModelName.Auto,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Non-SEC PDF path — mirrors v1 FinancialAnalyzer._handle_pdf_url_question."""
        try:
            clean_question = strip_url_from_text(question, pdf_url)
            company_data = self.company_connector.get_fundamental_data(ticker)
            company_name = company_data.name if company_data else ticker.upper()

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

                IMPORTANT: Always respond in the same language as the CURRENT question above, regardless of the language used in previous conversation history.
                Answer in a professional, informative tone. Prioritize clarity and scannability over narrative flow.
            """.strip()

            agent = MultiAgent(model_name=preferred_model)

            try:
                for chunk in agent.generate_content_with_pdf_url(
                    prompt=prompt,
                    pdf_url=pdf_url,
                    filename=f"{ticker.lower()}_document.pdf",
                    pdf_engine="pdf-text",
                ):
                    yield {"type": "answer", "body": chunk}
            except Exception as e:
                logger.error("Error generating content with PDF URL: %s", e)
                yield {
                    "type": "answer",
                    "body": "❌ Error processing PDF document. The file may be inaccessible or too large.",
                }
                return

            yield {"type": "model_used", "body": agent.model_name}

        except Exception as e:
            logger.error("Error handling PDF URL question: %s", e)
            yield {"type": "answer", "body": "❌ Error analyzing document. Please try again later."}

    async def analyze_question(
        self,
        ticker: str,
        question: str,
        use_url_context: bool = False,
        deep_analysis: bool = False,
        preferred_model: ModelName = ModelName.Auto,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        conversation_id: Optional[str] = None,
        anon_user_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        _ = (conversation_id, anon_user_id)

        extracted_url = extract_first_url(question)
        force_google_search_reason: str | None = None
        if extracted_url:
            if is_sec_filing_url(extracted_url):
                logger.info(
                    "SEC filing URL detected: %s. Forcing search decision (sec_url).",
                    extracted_url,
                )
                yield {"type": "attachment_url", "body": extracted_url}
                yield thinking_status("Reading SEC filing...", phase=AnalysisPhase.SEARCH, step=2)
                force_google_search_reason = "sec_url"
            else:
                is_valid, error_message = validate_pdf_url(extracted_url)
                if not is_valid:
                    yield {"type": "answer", "body": f"❌ {error_message}"}
                    return
                yield {"type": "attachment_url", "body": extracted_url}
                yield thinking_status(
                    "Reading the attached document...",
                    phase=AnalysisPhase.ANALYZE,
                    step=2,
                )
                async for chunk in self._handle_pdf_url_question(ticker, question, extracted_url, preferred_model):
                    yield chunk
                return

        normalized_ticker = ticker.strip().upper() if ticker else ""
        if normalized_ticker in ["UNDEFINED", "NULL", ""] or normalized_ticker == "NONE":
            normalized_ticker = "none"

        available_periods: dict[str, list] | None = None
        available_metrics: list[str] | None = None
        if normalized_ticker and normalized_ticker != "none":
            try:
                available_periods = self.company_financial_connector.get_available_periods(normalized_ticker)
                available_metrics = self.company_financial_connector.get_available_metrics(normalized_ticker)
            except Exception as e:
                logger.warning(
                    "Failed to fetch available periods/metrics for %s: %s",
                    normalized_ticker,
                    e,
                )

        decision_coro = self.search_decision_engine.decide(
            question=question,
            ticker=normalized_ticker,
            is_etf=False,
            force_google_search_reason=force_google_search_reason,
            available_periods=available_periods,
            available_metrics=available_metrics,
        )
        classify_coro = self.classifier.classify_question_type(
            question, ticker, conversation_messages=conversation_messages
        )
        decision, classify_result = await asyncio.gather(decision_coro, classify_coro)
        classification, comparison_tickers = classify_result

        status_ticker = normalized_ticker if normalized_ticker != "none" else None
        if decision.use_google_search:
            body = (
                f"Searching for the latest {status_ticker} data..."
                if status_ticker
                else "Searching for the latest data..."
            )
        else:
            body = f"Found {status_ticker} data in our database" if status_ticker else "Using data from our database"
        yield thinking_status(body, phase=AnalysisPhase.SEARCH, step=2)

        if not classification:
            yield {"type": "answer", "body": "❌ Unable to classify question type"}
            return

        request_id = str(uuid.uuid4())

        if classification == QuestionType.COMPANY_GENERAL.value:
            iterator = self.company_general_handler.handle(
                ticker=ticker,
                question=question,
                search_decision=decision,
                use_url_context=use_url_context,
                preferred_model=preferred_model,
                conversation_messages=conversation_messages,
                request_id=request_id,
            )
        elif classification == QuestionType.GENERAL_FINANCE.value:
            iterator = self.general_finance_handler.handle(
                question=question,
                search_decision=decision,
                use_url_context=use_url_context,
                preferred_model=preferred_model,
                conversation_messages=conversation_messages,
                request_id=request_id,
            )
        elif classification == QuestionType.COMPANY_SPECIFIC_FINANCE.value:
            iterator = self.company_specific_finance_handler.handle(
                ticker=ticker,
                question=question,
                search_decision=decision,
                use_url_context=use_url_context,
                deep_analysis=deep_analysis,
                preferred_model=preferred_model,
                conversation_messages=conversation_messages,
                available_metrics=available_metrics,
                request_id=request_id,
            )
        elif classification == QuestionType.COMPANY_COMPARISON.value:
            iterator = self.comparison_handler.handle(
                tickers=comparison_tickers or [ticker],
                question=question,
                search_decision=decision,
                short_analysis=not deep_analysis,
                preferred_model=preferred_model,
                conversation_messages=conversation_messages,
                request_id=request_id,
            )
        else:
            yield {"type": "answer", "body": f"❌ Unsupported question type: {classification}"}
            return

        async for chunk in iterator:
            yield chunk
