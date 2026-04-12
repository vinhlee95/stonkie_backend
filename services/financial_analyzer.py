"""Financial analyzer service - main entry point for question analysis."""

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from langfuse import observe

from agent.agent import Agent
from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.company import CompanyConnector
from connectors.company_financial import CompanyFinancialConnector
from connectors.conversation_store import get_conversation_meta, set_conversation_meta
from services.question_analyzer import CompanySpecificFinanceHandler
from services.question_analyzer.classifier import QuestionClassifier
from services.question_analyzer.comparison_handler import CompanyComparisonHandler
from services.question_analyzer.data_optimizer import FinancialDataOptimizer
from services.question_analyzer.handlers import (
    CompanyGeneralHandler,
    GeneralFinanceHandler,
)
from services.question_analyzer.types import AnalysisPhase, QuestionType, thinking_status
from services.search_decision_engine import SearchDecisionEngine
from utils.url_helper import extract_first_url, is_sec_filing_url, strip_url_from_text, validate_pdf_url
from utils.visual_stream import VisualAnswerStreamSplitter

logger = logging.getLogger(__name__)


class FinancialAnalyzer:
    """Main service for analyzing financial questions and generating insights."""

    def __init__(
        self,
        agent: Optional[Agent] = None,
        company_connector: Optional[CompanyConnector] = None,
        company_financial_connector: Optional[CompanyFinancialConnector] = None,
        search_decision_engine: Optional[SearchDecisionEngine] = None,
    ):
        self.agent = agent or Agent(model_type="gemini")
        self.company_connector = company_connector or CompanyConnector()
        self.company_financial_connector = company_financial_connector or CompanyFinancialConnector()
        self.search_decision_engine = search_decision_engine or SearchDecisionEngine()

        # Initialize components
        self.classifier = QuestionClassifier()
        self.data_optimizer = FinancialDataOptimizer(self.company_financial_connector)

        # Initialize comparison handler
        self.comparison_handler = CompanyComparisonHandler()

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
        use_url_context: bool = False,
        deep_analysis: bool = False,
        preferred_model: ModelName = ModelName.Auto,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        conversation_id: Optional[str] = None,
        anon_user_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        t_start = time.perf_counter()

        yield thinking_status("Classifying your question...", phase=AnalysisPhase.CLASSIFY, step=1)

        # Check for PDF URL in question
        extracted_url = extract_first_url(question)
        force_google_search_reason = None
        if extracted_url:
            if is_sec_filing_url(extracted_url):
                logger.info(f"SEC filing URL detected: {extracted_url}. Using Google Search to access content.")
                yield {"type": "attachment_url", "body": extracted_url}
                yield thinking_status("Searching SEC filing via Google...", phase=AnalysisPhase.SEARCH, step=2)
                force_google_search_reason = "sec_url"
            else:
                is_valid, error_message = validate_pdf_url(extracted_url)
                if not is_valid:
                    yield {"type": "answer", "body": f"❌ {error_message}"}
                    return
                yield {"type": "attachment_url", "body": extracted_url}
                yield thinking_status("Analyzing PDF document from URL...", phase=AnalysisPhase.ANALYZE, step=2)
                async for chunk in self._handle_pdf_url_question(ticker, question, extracted_url, preferred_model):
                    yield chunk
                return

        # Normalize ticker
        normalized_ticker = ticker.strip().upper() if ticker else ""
        if normalized_ticker in ["UNDEFINED", "NULL", ""]:
            normalized_ticker = "none"

        if conversation_messages:
            num_pairs = len(conversation_messages) // 2
            logger.info(
                f"🔄 Passing {num_pairs} Q/A pair(s) of conversation context to handler "
                f"(ticker: {normalized_ticker}, question: {question[:50]}...)"
            )

        # Fetch available DB periods and metrics for data-aware search decision
        available_periods = None
        available_metrics = None
        if normalized_ticker and normalized_ticker != "none":
            try:
                available_periods = self.company_financial_connector.get_available_periods(normalized_ticker)
                available_metrics = self.company_financial_connector.get_available_metrics(normalized_ticker)
            except Exception as e:
                logger.warning(f"Failed to fetch available periods/metrics for {normalized_ticker}: {e}")

        # Build search decision coroutine (always needed)
        search_coro = self.search_decision_engine.decide(
            question=question,
            ticker=normalized_ticker,
            is_etf=False,
            force_google_search_reason=force_google_search_reason,
            available_periods=available_periods,
            available_metrics=available_metrics,
        )

        # Sticky routing: reuse last classification for ambiguous follow-ups
        classification = None
        if conversation_messages and conversation_id and anon_user_id:
            is_ambiguous = len(question.split()) < 10 and not any(
                keyword in question.lower()
                for keyword in [
                    "revenue",
                    "profit",
                    "margin",
                    "earnings",
                    "cash flow",
                    "debt",
                    "assets",
                    "quarterly",
                    "annual",
                    "financial",
                    "doanh thu",
                    "lợi nhuận",
                    "biên lợi nhuận",
                    "thu nhập",
                    "dòng tiền",
                    "nợ",
                    "tài sản",
                    "quý",
                    "năm",
                    "tài chính",
                ]
            )
            if is_ambiguous:
                meta = get_conversation_meta(anon_user_id, normalized_ticker, conversation_id)
                last_question_type = meta.get("last_question_type")
                if last_question_type and last_question_type != QuestionType.COMPANY_COMPARISON.value:
                    classification = last_question_type
                    logger.info(
                        f"📌 Sticky routing: Reusing last classification '{classification}' "
                        f"for ambiguous follow-up question"
                    )

        # Run search decision + question classification in parallel when possible
        comparison_tickers = None
        if not classification:
            classify_coro = self.classifier.classify_question_type(
                question, ticker, conversation_messages=conversation_messages
            )
            t_parallel_block = time.perf_counter()
            decision, classify_result = await asyncio.gather(search_coro, classify_coro)
            logger.info(
                "Profiling parallel classify_question_type + search_decision_engine.decide: %.4fs",
                time.perf_counter() - t_parallel_block,
            )
            classification, comparison_tickers = classify_result
            logger.info(f"Question classified as: {classification}")

            if classification and conversation_id and anon_user_id:
                set_conversation_meta(
                    anon_user_id, normalized_ticker, conversation_id, {"last_question_type": classification}
                )
        else:
            t_search_only = time.perf_counter()
            decision = await search_coro
            logger.info(
                "Profiling search_decision_engine.decide only (sticky classification, no classify): %.4fs",
                time.perf_counter() - t_search_only,
            )

        use_google_search = decision.use_google_search

        # Yield search decision metadata
        yield {
            "type": "search_decision_meta",
            "body": {
                "search_decision": "on" if use_google_search else "off",
                "reason_code": decision.reason_code,
                "decision_model": decision.decision_model,
                "decision_fallback": decision.decision_fallback,
                "confidence": decision.confidence,
            },
        }
        if use_google_search:
            yield thinking_status("Searching the web for up-to-date data...", phase=AnalysisPhase.SEARCH, step=2)
        else:
            yield thinking_status("Using cached data for faster response", phase=AnalysisPhase.SEARCH, step=2)

        if not classification:
            yield {"type": "answer", "body": "❌ Unable to classify question type"}
            return

        # Handle comparison questions
        if classification == QuestionType.COMPANY_COMPARISON.value and comparison_tickers:
            short_analysis = not deep_analysis
            comparison_splitter = VisualAnswerStreamSplitter()
            async for chunk in self.comparison_handler.handle(
                tickers=comparison_tickers,
                question=question,
                use_google_search=use_google_search,
                short_analysis=short_analysis,
                preferred_model=preferred_model,
                conversation_messages=conversation_messages,
            ):
                if chunk.get("type") == "answer" and isinstance(chunk.get("body"), str):
                    for visual_event in comparison_splitter.process_text(chunk["body"]):
                        yield visual_event
                else:
                    yield chunk
            for visual_event in comparison_splitter.finalize():
                yield visual_event
            return

        handler = self.handlers.get(classification)
        if not handler:
            yield {"type": "answer", "body": "❌ Unable to find handler for question type"}
            return

        # Route to handler and track sources
        t_handler = time.perf_counter()
        has_sources = False
        visual_splitter = VisualAnswerStreamSplitter()

        if classification == QuestionType.GENERAL_FINANCE.value:
            handler_gen = handler.handle(
                question,
                use_google_search,
                use_url_context,
                preferred_model,
                conversation_messages=conversation_messages,
            )
        elif classification == QuestionType.COMPANY_GENERAL.value:
            handler_gen = handler.handle(
                ticker,
                question,
                use_google_search,
                use_url_context,
                preferred_model,
                conversation_messages=conversation_messages,
            )
        elif classification == QuestionType.COMPANY_SPECIFIC_FINANCE.value:
            handler_gen = handler.handle(
                ticker,
                question,
                use_google_search,
                use_url_context,
                deep_analysis,
                preferred_model,
                conversation_messages=conversation_messages,
            )
        else:
            return

        async for chunk in handler_gen:
            chunk_type = chunk.get("type")
            if chunk_type == "google_search_ground" and chunk.get("url"):
                has_sources = True
            elif chunk_type == "sources" and chunk.get("body"):
                has_sources = True
            elif chunk_type == "sources_grouped":
                grouped = (chunk.get("body") or {}).get("sources") if isinstance(chunk.get("body"), dict) else []
                if grouped:
                    has_sources = True
            if chunk_type == "answer" and isinstance(chunk.get("body"), str):
                for visual_event in visual_splitter.process_text(chunk.get("body")):
                    yield visual_event
            else:
                yield chunk

        for visual_event in visual_splitter.finalize():
            yield visual_event

        if use_google_search and not has_sources:
            logger.warning("Search attempt completed with no sources/citations.")
            yield thinking_status(
                "Web search returned no results — using model knowledge",
                phase=AnalysisPhase.SEARCH,
                step=2,
            )

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

                IMPORTANT: Always respond in the same language as the CURRENT question above, regardless of the language used in previous conversation history.
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
