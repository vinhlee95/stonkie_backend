"""V2 question handlers for Brave-grounded analyze flow."""

from __future__ import annotations

import logging
import os
from typing import Any, AsyncGenerator, Dict, List, Optional

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.brave_client import BraveClient
from connectors.company import CompanyConnector
from core.financial_statement_type import FinancialStatementType
from services.analysis_progress import AnalysisPhase, thinking_status
from services.analyze_retrieval.citation_index import build_sources_event
from services.analyze_retrieval.market import resolve_market
from services.analyze_retrieval.retrieval import retrieve_for_analyze
from services.question_analyzer.classifier import QuestionClassifier
from services.question_analyzer.context_builders import ContextBuilderInput, get_context_builder
from services.question_analyzer.context_builders.components import PromptComponents
from services.question_analyzer.data_optimizer import FinancialDataOptimizer
from services.question_analyzer.types import FinancialDataRequirement
from services.search_decision_engine import SearchDecision
from utils.conversation_format import format_conversation_context

logger = logging.getLogger(__name__)


class CompanyGeneralHandlerV2:
    """Company-general v2 handler with SearchDecision-based control flow."""

    def __init__(self, company_connector: Optional[CompanyConnector] = None):
        self.company_connector = company_connector or CompanyConnector()

    async def _generate_related_questions(
        self, original_question: str, preferred_model: ModelName
    ) -> AsyncGenerator[Dict[str, str], None]:
        prompt = f"""
Based on this original question: "{original_question}"
Generate exactly 3 high-quality follow-up questions, one per line.
Do not add numbering.
        """.strip()

        agent = MultiAgent(model_name=preferred_model)
        for question in agent.generate_content_by_lines(
            prompt=prompt,
            use_google_search=False,
            max_lines=3,
            min_line_length=10,
            strip_numbering=True,
            strip_markdown=True,
        ):
            yield {"type": "related_question", "body": question}

    async def handle(
        self,
        ticker: str,
        question: str,
        search_decision: SearchDecision,
        use_url_context: bool,
        preferred_model: ModelName = ModelName.Auto,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        request_id: str = "request-unknown",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        company = self.company_connector.get_by_ticker(ticker)
        company_name = company.name if company else ticker.upper()

        yield thinking_status(
            f"Analyzing {company_name} ({ticker})...",
            phase=AnalysisPhase.ANALYZE,
            step=3,
            total_steps=4,
        )

        conversation_context = ""
        if conversation_messages:
            formatted = format_conversation_context(conversation_messages, ticker, company_name)
            if formatted:
                conversation_context = f"\n\n{formatted}\n"

        retrieved_sources = []
        sources_context = ""
        if search_decision.use_google_search:
            market = resolve_market(getattr(company, "country", None), question)
            brave_client = BraveClient(api_key=os.getenv("BRAVE_API_KEY", ""))
            retrieval_result = retrieve_for_analyze(
                question=question,
                market=market,
                request_id=request_id,
                brave_client=brave_client,
                ticker=ticker.upper(),
            )
            retrieved_sources = retrieval_result.sources

            trusted_publishers: list[str] = []
            for source in retrieved_sources:
                if not source.is_trusted:
                    continue
                if source.publisher in trusted_publishers:
                    continue
                trusted_publishers.append(source.publisher)

            if trusted_publishers:
                publisher_list = ", ".join(trusted_publishers)
                yield thinking_status(
                    f"Reading {len(retrieved_sources)} sources: {publisher_list}",
                    phase=AnalysisPhase.SEARCH,
                    step=2,
                    total_steps=4,
                )

            source_lines = []
            for idx, source in enumerate(retrieved_sources, start=1):
                source_lines.append(f"[{idx}] {source.title}\n{source.url}")
            if source_lines:
                sources_context = "\n\nSources:\n" + "\n\n".join(source_lines)

        prompt = f"""
You are an expert about a business.
Answer this question about {company_name} (ticker: {ticker}):
{question}
IMPORTANT: Always respond in same language as the current question.
Keep response concise under 200 words.
Use short paragraphs and bullet points for readability.
If you cite sources, use inline markers like [1], [2].
When sources are provided, ground your answer in them and cite with matching [N] markers only.
{conversation_context}
{sources_context}
        """.strip()

        agent = MultiAgent(model_name=preferred_model)
        full_text_chunks: list[str] = []
        for chunk in agent.generate_content(prompt=prompt, use_google_search=False):
            if not isinstance(chunk, str):
                continue
            full_text_chunks.append(chunk)
            yield {"type": "answer", "body": chunk}

        if retrieved_sources:
            yield build_sources_event("".join(full_text_chunks), retrieved_sources)

        yield {"type": "model_used", "body": agent.model_name}

        async for related_q in self._generate_related_questions(question, preferred_model):
            yield related_q


def _trusted_publisher_status(retrieved_sources, *, ticker_list: Optional[List[str]] = None):
    trusted_publishers: list[str] = []
    for source in retrieved_sources:
        if not source.is_trusted:
            continue
        if source.publisher in trusted_publishers:
            continue
        trusted_publishers.append(source.publisher)
    if not trusted_publishers:
        return None
    publisher_list = ", ".join(trusted_publishers)
    if ticker_list:
        body = f"Reading {len(retrieved_sources)} sources across " f"{', '.join(ticker_list)}: {publisher_list}"
    else:
        body = f"Reading {len(retrieved_sources)} sources: {publisher_list}"
    return thinking_status(body, phase=AnalysisPhase.SEARCH, step=2, total_steps=4)


def _build_sources_block(retrieved_sources) -> str:
    if not retrieved_sources:
        return ""
    lines = []
    for idx, source in enumerate(retrieved_sources, start=1):
        lines.append(f"[{idx}] {source.title}\n{source.url}")
    return "\n\nSources:\n" + "\n\n".join(lines)


class GeneralFinanceHandlerV2:
    """General-finance v2 handler (Brave-grounded when search_decision allows)."""

    async def _generate_related_questions(
        self, original_question: str, preferred_model: ModelName
    ) -> AsyncGenerator[Dict[str, str], None]:
        prompt = f"""
Based on this original question: "{original_question}"
Generate exactly 3 high-quality follow-up questions, one per line.
Do not add numbering.
        """.strip()

        agent = MultiAgent(model_name=preferred_model)
        for question in agent.generate_content_by_lines(
            prompt=prompt,
            use_google_search=False,
            max_lines=3,
            min_line_length=10,
            strip_numbering=True,
            strip_markdown=True,
        ):
            yield {"type": "related_question", "body": question}

    async def handle(
        self,
        question: str,
        search_decision: SearchDecision,
        use_url_context: bool,
        preferred_model: ModelName = ModelName.Auto,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        request_id: str = "request-unknown",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        yield thinking_status(
            "Writing your answer...",
            phase=AnalysisPhase.ANALYZE,
            step=3,
            total_steps=4,
        )

        conversation_context = ""
        if conversation_messages:
            recent = conversation_messages[-4:] if len(conversation_messages) >= 4 else conversation_messages
            lines: list[str] = []
            for msg in recent:
                role = (msg.get("role") or "").upper()
                content = (msg.get("content") or "").strip()
                if content:
                    lines.append(f"{role}: {content}")
            if lines:
                conversation_context = "\n\nPrevious conversation:\n" + "\n".join(lines) + "\n"

        retrieved_sources = []
        sources_context = ""
        if search_decision.use_google_search:
            market = resolve_market(None, question)
            brave_client = BraveClient(api_key=os.getenv("BRAVE_API_KEY", ""))
            retrieval_result = retrieve_for_analyze(
                question=question,
                market=market,
                request_id=request_id,
                brave_client=brave_client,
                ticker=None,
            )
            retrieved_sources = retrieval_result.sources

            status_event = _trusted_publisher_status(retrieved_sources)
            if status_event is not None:
                yield status_event

            sources_context = _build_sources_block(retrieved_sources)

        citation_directive = (
            "If you cite sources, use inline markers like [1], [2] matching the Sources list below."
            if retrieved_sources
            else ""
        )

        prompt = f"""
Please explain this financial concept or answer this question:
{question}
IMPORTANT: Always respond in the same language as the current question.
Keep the answer under 150 words. Break into short paragraphs.
{citation_directive}
{conversation_context}
{sources_context}
        """.strip()

        agent = MultiAgent(model_name=preferred_model)
        full_chunks: list[str] = []
        for chunk in agent.generate_content(prompt=prompt, use_google_search=False):
            if not isinstance(chunk, str):
                continue
            full_chunks.append(chunk)
            yield {"type": "answer", "body": chunk}

        if retrieved_sources:
            yield build_sources_event("".join(full_chunks), retrieved_sources)

        yield {"type": "model_used", "body": agent.model_name}

        async for related_q in self._generate_related_questions(question, preferred_model):
            yield related_q


class CompanySpecificFinanceHandlerV2:
    """Company-specific financial v2 handler — DB context + optional Brave passages."""

    def __init__(
        self,
        company_connector: Optional[CompanyConnector] = None,
        classifier: Optional[QuestionClassifier] = None,
        data_optimizer: Optional[FinancialDataOptimizer] = None,
    ):
        self.company_connector = company_connector or CompanyConnector()
        self.classifier = classifier or QuestionClassifier()
        self.data_optimizer = data_optimizer or FinancialDataOptimizer()

    async def _generate_related_questions(
        self, original_question: str, preferred_model: ModelName
    ) -> AsyncGenerator[Dict[str, str], None]:
        prompt = f"""
Based on this original question: "{original_question}"
Generate exactly 3 high-quality follow-up questions, one per line.
Do not add numbering.
        """.strip()
        agent = MultiAgent(model_name=preferred_model)
        for question in agent.generate_content_by_lines(
            prompt=prompt,
            use_google_search=False,
            max_lines=3,
            min_line_length=10,
            strip_numbering=True,
            strip_markdown=True,
        ):
            yield {"type": "related_question", "body": question}

    async def _stream_fallback_answer(
        self,
        question: str,
        ticker: str,
        company_name: str,
        conversation_messages: List[Dict[str, str]],
        preferred_model: ModelName,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        conversation_context = format_conversation_context(conversation_messages, ticker or "the company", company_name)
        prompt = f"""{PromptComponents.current_date()}

Based on our previous conversation, answer this follow-up question:

{conversation_context}

Current question: {question}

IMPORTANT: Always respond in the same language as the CURRENT question above.
Provide a helpful, general answer that builds on what we discussed before."""

        agent = MultiAgent(model_name=preferred_model)
        for chunk in agent.generate_content(prompt=prompt, use_google_search=False):
            if not isinstance(chunk, str):
                continue
            yield {"type": "answer", "body": chunk}
        yield {"type": "model_used", "body": agent.model_name}

        async for related_q in self._generate_related_questions(question, preferred_model):
            yield related_q

    async def handle(
        self,
        ticker: str,
        question: str,
        search_decision: SearchDecision,
        use_url_context: bool,
        deep_analysis: bool = False,
        preferred_model: ModelName = ModelName.Auto,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        available_metrics: Optional[list[str]] = None,
        request_id: str = "request-unknown",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        ticker_norm = (ticker or "").lower().strip()

        # Fallback 1: missing/undefined ticker + conversation
        if (not ticker_norm or ticker_norm in ["undefined", "null", "none", ""]) and conversation_messages:
            yield thinking_status(
                "Continuing from our previous conversation...",
                phase=AnalysisPhase.ANALYZE,
                step=3,
                total_steps=4,
            )
            async for event in self._stream_fallback_answer(
                question, ticker_norm, "", conversation_messages, preferred_model
            ):
                yield event
            return

        yield thinking_status(
            f"Figuring out what {ticker_norm.upper()} data you need...",
            phase=AnalysisPhase.CLASSIFY,
            step=3,
            total_steps=6,
        )

        (
            data_requirement,
            period_requirement,
            relevant_statements,
        ) = await self.classifier.classify_data_and_period_requirement(
            ticker_norm, question, available_metrics=available_metrics
        )

        if period_requirement is not None:
            yield thinking_status(
                f"Loading {ticker_norm.upper()} {period_requirement.period_type} financial reports...",
                phase=AnalysisPhase.DATA_FETCH,
                step=4,
                total_steps=6,
            )

        (
            company_fundamental,
            annual_statements,
            quarterly_statements,
        ) = await self.data_optimizer.fetch_optimized_data(
            ticker=ticker_norm,
            data_requirement=data_requirement,
            period_requirement=period_requirement,
        )

        if relevant_statements and data_requirement == FinancialDataRequirement.DETAILED:
            valid_types = set(FinancialStatementType)
            drop_types = valid_types - set(relevant_statements)
            if drop_types:
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

        # Fallback 2: no DB data + conversation
        has_no_data = (
            (not company_fundamental or not company_fundamental.get("Name"))
            and len(annual_statements) == 0
            and len(quarterly_statements) == 0
        )
        if has_no_data and conversation_messages and data_requirement != FinancialDataRequirement.NONE:
            yield thinking_status(
                f"No {ticker_norm.upper()} financials available — answering from conversation context",
                phase=AnalysisPhase.ANALYZE,
                step=5,
                total_steps=6,
            )
            company_name = company_fundamental.get("Name", "") if company_fundamental else ""
            async for event in self._stream_fallback_answer(
                question, ticker_norm, company_name, conversation_messages, preferred_model
            ):
                yield event
            return

        yield thinking_status(
            f"Analyzing {ticker_norm.upper()} financials...",
            phase=AnalysisPhase.ANALYZE,
            step=5,
            total_steps=6,
        )

        builder = get_context_builder(data_requirement)
        financial_context = builder.build(
            ContextBuilderInput(
                ticker=ticker_norm,
                question=question,
                company_fundamental=company_fundamental,
                annual_statements=annual_statements,
                quarterly_statements=quarterly_statements,
                deep_analysis=deep_analysis,
                use_google_search=False,
            )
        )

        conversation_context = ""
        if conversation_messages:
            company_name = (company_fundamental or {}).get("Name", "")
            conversation_context = (
                "\n\n" + format_conversation_context(conversation_messages, ticker_norm, company_name) + "\n"
            )

        retrieved_sources = []
        sources_block = ""
        if search_decision.use_google_search:
            country = (company_fundamental or {}).get("Country") or (company_fundamental or {}).get("country")
            market = resolve_market(country, question)
            brave_client = BraveClient(api_key=os.getenv("BRAVE_API_KEY", ""))
            retrieval_result = retrieve_for_analyze(
                question=question,
                market=market,
                request_id=request_id,
                brave_client=brave_client,
                ticker=ticker_norm.upper(),
            )
            retrieved_sources = retrieval_result.sources
            status_event = _trusted_publisher_status(retrieved_sources)
            if status_event is not None:
                yield status_event
            sources_block = _build_sources_block(retrieved_sources)

        analysis_prompt = PromptComponents.analysis_focus()
        source_prompt = PromptComponents.source_instructions()
        visual_prompt = PromptComponents.visual_output_instructions()
        combined_prompt = (
            f"{financial_context}{conversation_context}\n\n"
            f"{analysis_prompt}\n\n{source_prompt}\n\n{visual_prompt}"
            f"{sources_block}"
        )

        agent = MultiAgent(model_name=preferred_model)
        full_chunks: list[str] = []
        for chunk in agent.generate_content(prompt=combined_prompt, use_google_search=False):
            if not isinstance(chunk, str):
                continue
            full_chunks.append(chunk)
            yield {"type": "answer", "body": chunk}

        if retrieved_sources:
            yield build_sources_event("".join(full_chunks), retrieved_sources)

        yield {"type": "model_used", "body": agent.model_name}

        async for related_q in self._generate_related_questions(question, preferred_model):
            yield related_q
