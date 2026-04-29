"""V2 question handlers for Brave-grounded analyze flow."""

from __future__ import annotations

import logging
import os
from typing import Any, AsyncGenerator, Dict, List, Optional

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.brave_client import BraveClient
from connectors.company import CompanyConnector
from services.analysis_progress import AnalysisPhase, thinking_status
from services.analyze_retrieval.citation_index import build_sources_event
from services.analyze_retrieval.market import resolve_market
from services.analyze_retrieval.retrieval import retrieve_for_analyze
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
