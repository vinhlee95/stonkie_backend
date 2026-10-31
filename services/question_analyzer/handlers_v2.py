"""V2 question handlers for Brave-grounded analyze flow."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing
from typing import Any, AsyncGenerator, AsyncIterator, Callable, Dict, Iterable, Iterator, List, Optional, TypeVar

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.brave_client import BraveClient
from connectors.company import CompanyConnector
from core.financial_statement_type import FinancialStatementType
from services.analysis_progress import AnalysisPhase, ThinkingProgress
from services.analyze_retrieval.citation_index import build_sources_event
from services.analyze_retrieval.freshness import build_temporal_context_block
from services.analyze_retrieval.market import resolve_market
from services.analyze_retrieval.query_reformulator import PrecomputedQueryReformulator
from services.analyze_retrieval.retrieval import retrieve_for_analyze
from services.analyze_retrieval.schemas import AnalyzePassage
from services.analyze_retrieval.url_ingest import UrlIngestError, ingest_url
from services.question_analyzer.context_builders import ContextBuilderInput, get_context_builder
from services.question_analyzer.context_builders.components import PromptComponents
from services.question_analyzer.data_optimizer import FinancialDataOptimizer
from services.question_analyzer.router import RoutingResult
from services.question_analyzer.types import FinancialDataRequirement, FinancialPeriodRequirement
from services.search_decision_engine import SearchDecision
from utils.async_helpers import iterate_in_thread
from utils.conversation_format import format_conversation_context
from utils.url_helper import extract_first_url

logger = logging.getLogger(__name__)


_GROUNDING_RULES = PromptComponents.grounding_rules()
_NO_DATA_DECLINE = PromptComponents.no_data_decline()
_URL_GROUNDED_RULES = (
    "**URL-grounded document rules (override all other data rules for this answer):**\n"
    "- Use ONLY the Source passages below to answer this URL-grounded question.\n"
    "- Do not use training knowledge, the report URL itself, or database financial statements unless the same fact is explicitly present in the Source passages.\n"
    "- If the Source passages do not contain enough information to answer the question, say that the extracted document chunks do not contain enough detail.\n"
    "- Do not search for additional sources or infer missing filing details from memory.\n"
    "- Do not write citations, source notes, source lists, page notes, URLs, or bibliography sections anywhere in the answer. Forbidden examples: `[1]`, `Excerpt 2`, `(Operating Income on page 29)`, `(Source: ...)`, `according to ...`, and `Sources:`.\n"
    "- Ignore any earlier instruction that asks for source notes after paragraphs or sections.\n"
    "- The extracted document context is internal grounding material only. Do not name or cite extracted paragraphs in the final answer.\n"
    "- End after the analysis itself. Do not append a `Sources:` section or any other citation/source section."
)
_URL_FINAL_RESPONSE_FORMAT = (
    "\n\n**Final response format for URL-grounded answers (must follow exactly):**\n"
    "1. Write only the answer body.\n"
    "2. Do not include inline citations, source notes, page notes, URLs, bracketed markers, extracted paragraph labels, source lists, or bibliography sections.\n"
    "3. Do not include a `Sources:` section.\n"
    "4. Stop immediately after the final analysis sentence or bullet."
)
_STABLE_CONCEPT_ALLOW = (
    "This is a well-known, stable fact (e.g. founding date, founders, headquarters). "
    "You may answer from your training knowledge. Be concise and confident."
)


def _select_grounding(
    retrieved_sources: list, search_decision: SearchDecision, *, is_url_grounded: bool = False
) -> str:
    if is_url_grounded:
        return _URL_GROUNDED_RULES
    if retrieved_sources:
        return _GROUNDING_RULES
    if search_decision.reason_code == "stable_concept":
        return _STABLE_CONCEPT_ALLOW
    return _NO_DATA_DECLINE


def _iter_answer_chunks(chunks: Iterable[Any]) -> Iterator[str]:
    """Lazily yield text chunks so each one streams as soon as the LLM produces it.

    LLM generators yield ``str | dict`` (url_citation dicts); dicts are dropped.
    """
    for chunk in chunks:
        if isinstance(chunk, str):
            yield chunk


T = TypeVar("T")

# Answer streams hold a worker for their whole length; their own pool keeps a burst of answers from
# starving the default executor that retrieval, DB and related-questions to_thread calls share.
ANSWER_LLM_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="analyze-answer-llm")


def _answer_stream(stream: Iterable[T]) -> aclosing[AsyncIterator[T]]:
    """Pull a blocking LLM stream (e.g. ``_iter_answer_chunks(...)``) in ANSWER_LLM_POOL, one item per step,
    so the event loop serves other requests while the answer streams.

    Use as ``async with _answer_stream(...) as chunks``: leaving the block (client disconnect, error) closes
    the LLM stream right away, and a cancelled consumer stops pulling after the item in flight.
    """
    return aclosing(iterate_in_thread(stream, ANSWER_LLM_POOL))


_RELATED_QUESTIONS_MODEL = ModelName.Gemini31FlashLite


def _related_questions_sync(prompt: str, max_lines: int) -> list[str]:
    agent = MultiAgent(model_name=_RELATED_QUESTIONS_MODEL)
    return list(
        agent.generate_content_by_lines(
            prompt=prompt,
            use_google_search=False,
            max_lines=max_lines,
            min_line_length=10,
            strip_numbering=True,
            strip_markdown=True,
        )
    )


async def generate_related_questions(
    original_question: str, *, comparison: bool = False, count: int = 3
) -> AsyncGenerator[Dict[str, str], None]:
    """Follow-up questions for every v2 handler; the LLM call runs in a worker thread."""
    if comparison:
        prompt = f"""
Based on this comparison question: "{original_question}"
Generate {count} follow-up comparison questions, one per line.
        """.strip()
    else:
        prompt = f"""
Based on this original question: "{original_question}"
Generate exactly {count} high-quality follow-up questions, one per line.
Do not add numbering.
        """.strip()
    for question in await asyncio.to_thread(_related_questions_sync, prompt, count):
        yield {"type": "related_question", "body": question}


async def start_related_questions(events: AsyncIterator[Dict[str, str]]) -> asyncio.Task:
    """Run the related-questions generator concurrently with the answer; await the task after model_used.

    Callers must cancel the task (no-op once done) when the answer fails or the client disconnects.
    """

    async def _collect() -> list[Dict[str, str]]:
        return [event async for event in events]

    task = asyncio.ensure_future(_collect())
    await asyncio.sleep(0)  # let it hand its LLM call to a worker thread before the answer stream blocks the loop
    return task


async def related_questions_result(task: asyncio.Task) -> list[Dict[str, str]]:
    try:
        return await task
    except Exception:
        logger.warning("Related questions generation failed", exc_info=True)
        return []


_FILING_REQUIREMENTS = (FinancialDataRequirement.QUARTERLY_SUMMARY, FinancialDataRequirement.ANNUAL_SUMMARY)


_PERIOD_WORDING = {"annual": "annual", "quarterly": "quarterly", "both": "annual and quarterly"}


def _searching_status(progress: ThinkingProgress, query: str) -> dict:
    return progress.status(f'Searching for "{query}"...', phase=AnalysisPhase.SEARCH)


def _writing_status(
    progress: ThinkingProgress,
    retrieved_sources=(),
    *,
    ticker_list: Optional[List[str]] = None,
    lead: str = "",
) -> dict:
    """The last status before the answer LLM call; says what the answer is grounded on.

    Pass web sources only (not URL-grounded documents). ``lead`` prefixes a note, e.g. a failed retrieval.
    """
    body = "writing your answer"
    if retrieved_sources:
        count = len(retrieved_sources)
        body += f" from {count} source{'' if count == 1 else 's'}"
        if ticker_list and len(ticker_list) > 1:
            body += f" across {', '.join(ticker_list)}"
        trusted_publishers: list[str] = []
        for source in retrieved_sources:
            if source.is_trusted and source.publisher not in trusted_publishers:
                trusted_publishers.append(source.publisher)
        if trusted_publishers:
            body += f": {', '.join(trusted_publishers)}"
    body += "..."
    body = f"{lead}{body}" if lead else body[0].upper() + body[1:]
    return progress.status(body, phase=AnalysisPhase.ANALYZE)


def _period_wording(
    data_requirement: FinancialDataRequirement, period_requirement: Optional[FinancialPeriodRequirement]
) -> Optional[str]:
    """Human period wording for the statements fetch_optimized_data loads; None when it loads none."""
    if data_requirement == FinancialDataRequirement.QUARTERLY_SUMMARY:
        return "quarterly"
    if data_requirement == FinancialDataRequirement.ANNUAL_SUMMARY:
        return "annual"
    if data_requirement == FinancialDataRequirement.DETAILED and period_requirement is not None:
        return _PERIOD_WORDING.get(period_requirement.period_type)
    return None


def _retrieve_routed(
    routing: RoutingResult,
    *,
    market,
    request_id: str,
    ticker: str | None,
    company_name: str | None = None,
    company_name_lookup: Optional[Callable[[], Optional[str]]] = None,
    finished: Optional[threading.Event] = None,
):
    """Brave retrieval for the router's standalone question + queries (no reformulator LLM call). Blocking.
    `company_name_lookup` runs alongside the Brave searches; its name feeds the relevance filter."""

    def retrieve(resolve_company_name: Optional[Callable[[], Optional[str]]] = None):
        return retrieve_for_analyze(
            question=routing.standalone_question,
            market=market,
            request_id=request_id,
            brave_client=BraveClient(api_key=os.getenv("BRAVE_API_KEY", "")),
            ticker=ticker,
            company_name=company_name,
            query_reformulator=PrecomputedQueryReformulator(routing.search_queries),
            resolve_company_name=resolve_company_name,
        )

    try:
        if company_name_lookup is None:
            return retrieve()
        with ThreadPoolExecutor(max_workers=1) as pool:
            return retrieve(pool.submit(company_name_lookup).result)
    finally:
        if finished is not None:
            finished.set()


def _consume_task_exception(task: asyncio.Future) -> None:
    if not task.cancelled():
        task.exception()


def _build_prompt_debug_event(
    *,
    handler: str,
    prompt: str,
    retrieved_sources,
    selected_passages: list[AnalyzePassage] | None = None,
) -> dict[str, Any]:
    return {
        "type": "debug_prompt_context",
        "body": {
            "handler": handler,
            "prompt": prompt,
            "source_count": len(retrieved_sources or []),
            "selected_passage_count": len(selected_passages or []),
            "sources": [
                {
                    "source_id": source.id,
                    "title": source.title,
                    "url": source.url,
                    "publisher": source.publisher,
                    "published_at": source.published_at.isoformat() if source.published_at else None,
                    "is_trusted": source.is_trusted,
                }
                for source in retrieved_sources or []
            ],
            "selected_passages": [
                {
                    "source_id": passage.source_id,
                    "title": passage.title,
                    "url": passage.url,
                    "publisher": passage.publisher,
                    "passage_index": passage.passage_index,
                    "content": passage.content,
                }
                for passage in selected_passages or []
            ],
        },
    }


class CompanyGeneralHandlerV2:
    """Company-general v2 handler with SearchDecision-based control flow."""

    def __init__(self, company_connector: Optional[CompanyConnector] = None):
        self.company_connector = company_connector or CompanyConnector()

    def _generate_related_questions(self, original_question: str) -> AsyncIterator[Dict[str, str]]:
        return generate_related_questions(original_question)

    async def handle(
        self,
        ticker: str,
        question: str,
        search_decision: SearchDecision,
        use_url_context: bool,
        preferred_model: ModelName = ModelName.Auto,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        request_id: str = "request-unknown",
        debug_prompt_context: bool = False,
        *,
        routing: RoutingResult,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        progress = ThinkingProgress()
        company = self.company_connector.get_by_ticker(ticker)
        company_name = company.name if company else ticker.upper()

        conversation_context = ""
        if conversation_messages:
            formatted = format_conversation_context(conversation_messages, ticker, company_name)
            if formatted:
                conversation_context = f"\n\n{formatted}\n"

        retrieved_sources = []
        sources_context = ""
        selected_passages: list[AnalyzePassage] = []
        is_url_grounded = False
        user_url = extract_first_url(question) if use_url_context else None
        if user_url:
            yield progress.status("Reading the attached document...", phase=AnalysisPhase.SEARCH)
            try:
                url_result = _ingest_user_url(user_url, question, request_id)
            except UrlIngestError:
                logger.exception("v2 URL ingest failed for user URL", extra={"request_id": request_id, "url": user_url})
                yield _url_ingest_error_event()
                return
            retrieved_sources = [url_result.source]
            selected_passages = url_result.selected_passages
            is_url_grounded = True
            sources_context = _build_sources_block(retrieved_sources, selected_passages, use_excerpt_labels=True)
        elif search_decision.use_google_search:
            yield _searching_status(progress, routing.search_queries[0])
            retrieval_result = await asyncio.to_thread(
                _retrieve_routed,
                routing,
                market=resolve_market(getattr(company, "country", None), question),
                request_id=request_id,
                ticker=ticker.upper(),
                company_name=company_name,
            )
            retrieved_sources = retrieval_result.sources
            selected_passages = retrieval_result.selected_passages
            sources_context = _build_sources_block(retrieved_sources, selected_passages)

        grounding_directive = _select_grounding(
            retrieved_sources,
            search_decision,
            is_url_grounded=is_url_grounded,
        )

        temporal_context = build_temporal_context_block(question)
        prompt = f"""
You are an expert about a business.
Answer this question about {company_name} (ticker: {ticker}):
{question}
{temporal_context}
{grounding_directive}

IMPORTANT: Always respond in same language as the current question.
Keep response concise under 200 words.
Use short paragraphs and bullet points for readability.
{conversation_context}
{sources_context}
{_URL_FINAL_RESPONSE_FORMAT if is_url_grounded else ""}
        """.strip()

        if debug_prompt_context:
            yield _build_prompt_debug_event(
                handler="company_general_v2",
                prompt=prompt,
                retrieved_sources=retrieved_sources,
                selected_passages=selected_passages,
            )

        yield _writing_status(progress, [] if is_url_grounded else retrieved_sources)
        related_task = await start_related_questions(self._generate_related_questions(question))
        try:
            agent = MultiAgent(model_name=preferred_model)
            async with _answer_stream(
                _iter_answer_chunks(agent.generate_content(prompt=prompt, use_google_search=False))
            ) as chunks:
                async for chunk in chunks:
                    yield {"type": "answer", "body": chunk}

            if retrieved_sources and not is_url_grounded:
                yield build_sources_event(retrieved_sources)

            yield {"type": "model_used", "body": agent.model_name}

            for related_q in await related_questions_result(related_task):
                yield related_q
        finally:
            related_task.cancel()


def _url_ingest_error_event() -> dict[str, str]:
    return {
        "type": "error",
        "code": "url_ingest_failed",
        "body": "Could not read the document URL.",
    }


def _ingest_user_url(url: str, question: str, request_id: str):
    return ingest_url(
        url=url,
        question=question,
        request_id=request_id,
        source_kind="user_url",
    )


def _build_sources_block(
    retrieved_sources,
    selected_passages: list[AnalyzePassage] | None = None,
    *,
    use_excerpt_labels: bool = False,
) -> str:
    if not retrieved_sources:
        return ""
    passages_by_source_id: dict[str, list[AnalyzePassage]] = {}
    for passage in selected_passages or []:
        passages_by_source_id.setdefault(passage.source_id, []).append(passage)
    blocks = []
    for idx, source in enumerate(retrieved_sources, start=1):
        published = source.published_at.isoformat() if source.published_at else "null"
        passage_lines = []
        for passage in passages_by_source_id.get(source.id, []):
            if use_excerpt_labels:
                passage_lines.append(f"Extracted paragraph: {passage.content}")
            else:
                passage_lines.append(f"Passage {passage.passage_index}: {passage.content}")
        if not passage_lines:
            fallback_content = (source.raw_content or "").strip()
            if fallback_content:
                passage_lines.append(f"Content: {fallback_content}")
        block_lines = [
            f"Document {idx}" if use_excerpt_labels else f"Source {idx}",
            f"Title: {source.title}",
            f"URL: {source.url}",
            f"Published: {published}",
            *passage_lines,
        ]
        blocks.append("\n".join(block_lines))
    block_header = "Extracted document context" if use_excerpt_labels else "Sources"
    return f"\n\n{block_header}:\n" + "\n\n".join(blocks)


class GeneralFinanceHandlerV2:
    """General-finance v2 handler (Brave-grounded when search_decision allows)."""

    def _generate_related_questions(self, original_question: str) -> AsyncIterator[Dict[str, str]]:
        return generate_related_questions(original_question)

    async def handle(
        self,
        question: str,
        search_decision: SearchDecision,
        use_url_context: bool,
        preferred_model: ModelName = ModelName.Auto,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        request_id: str = "request-unknown",
        debug_prompt_context: bool = False,
        *,
        routing: RoutingResult,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        progress = ThinkingProgress()
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
        selected_passages: list[AnalyzePassage] = []
        is_url_grounded = False
        user_url = extract_first_url(question) if use_url_context else None
        if user_url:
            yield progress.status("Reading the attached document...", phase=AnalysisPhase.SEARCH)
            try:
                url_result = _ingest_user_url(user_url, question, request_id)
            except UrlIngestError:
                logger.exception("v2 URL ingest failed for user URL", extra={"request_id": request_id, "url": user_url})
                yield _url_ingest_error_event()
                return
            retrieved_sources = [url_result.source]
            selected_passages = url_result.selected_passages
            is_url_grounded = True
            sources_context = _build_sources_block(retrieved_sources, selected_passages, use_excerpt_labels=True)
        elif search_decision.use_google_search:
            yield _searching_status(progress, routing.search_queries[0])
            retrieval_result = await asyncio.to_thread(
                _retrieve_routed,
                routing,
                market=resolve_market(None, question),
                request_id=request_id,
                ticker=None,
            )
            retrieved_sources = retrieval_result.sources
            selected_passages = retrieval_result.selected_passages
            sources_context = _build_sources_block(retrieved_sources, selected_passages)

        grounding_directive = _select_grounding(
            retrieved_sources,
            search_decision,
            is_url_grounded=is_url_grounded,
        )

        temporal_context = build_temporal_context_block(question)
        prompt = f"""
Please explain this financial concept or answer this question:
{question}
{temporal_context}
{grounding_directive}

IMPORTANT: Always respond in the same language as the current question.
Keep the answer under 150 words. Break into short paragraphs.
{conversation_context}
{sources_context}
{_URL_FINAL_RESPONSE_FORMAT if is_url_grounded else ""}
        """.strip()

        if debug_prompt_context:
            yield _build_prompt_debug_event(
                handler="general_finance_v2",
                prompt=prompt,
                retrieved_sources=retrieved_sources,
                selected_passages=selected_passages,
            )

        yield _writing_status(progress, [] if is_url_grounded else retrieved_sources)
        related_task = await start_related_questions(self._generate_related_questions(question))
        try:
            agent = MultiAgent(model_name=preferred_model)
            async with _answer_stream(
                _iter_answer_chunks(agent.generate_content(prompt=prompt, use_google_search=False))
            ) as chunks:
                async for chunk in chunks:
                    yield {"type": "answer", "body": chunk}

            if retrieved_sources and not is_url_grounded:
                yield build_sources_event(retrieved_sources)

            yield {"type": "model_used", "body": agent.model_name}

            for related_q in await related_questions_result(related_task):
                yield related_q
        finally:
            related_task.cancel()


class CompanySpecificFinanceHandlerV2:
    """Company-specific financial v2 handler — DB context + optional Brave passages."""

    def __init__(
        self,
        company_connector: Optional[CompanyConnector] = None,
        data_optimizer: Optional[FinancialDataOptimizer] = None,
    ):
        self.company_connector = company_connector or CompanyConnector()
        self.data_optimizer = data_optimizer or FinancialDataOptimizer()

    def _generate_related_questions(self, original_question: str) -> AsyncIterator[Dict[str, str]]:
        return generate_related_questions(original_question)

    def _company_name(self, ticker: str) -> Optional[str]:
        company = self.company_connector.get_by_ticker(ticker)
        return company.name if company and company.name else None

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

        related_task = await start_related_questions(self._generate_related_questions(question))
        try:
            agent = MultiAgent(model_name=preferred_model)
            async with _answer_stream(
                _iter_answer_chunks(agent.generate_content(prompt=prompt, use_google_search=False))
            ) as chunks:
                async for chunk in chunks:
                    yield {"type": "answer", "body": chunk}
            yield {"type": "model_used", "body": agent.model_name}

            for related_q in await related_questions_result(related_task):
                yield related_q
        finally:
            related_task.cancel()

    async def handle(
        self,
        ticker: str,
        question: str,
        search_decision: SearchDecision,
        use_url_context: bool,
        deep_analysis: bool = False,
        preferred_model: ModelName = ModelName.Auto,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        request_id: str = "request-unknown",
        debug_prompt_context: bool = False,
        *,
        routing: RoutingResult,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        progress = ThinkingProgress()
        ticker_norm = (ticker or "").lower().strip()

        # Fallback 1: missing/undefined ticker + conversation
        if (not ticker_norm or ticker_norm in ["undefined", "null", "none", ""]) and conversation_messages:
            yield progress.status("Writing your answer from our conversation...", phase=AnalysisPhase.ANALYZE)
            async with aclosing(
                self._stream_fallback_answer(question, ticker_norm, "", conversation_messages, preferred_model)
            ) as events:
                async for event in events:
                    yield event
            return

        data_requirement = routing.data_requirement
        period_requirement = routing.period_requirement
        relevant_statements = routing.relevant_statements

        user_url = extract_first_url(question) if use_url_context else None

        # Router queries are known up front, so Brave runs while the DB fetch does. Not for filing summaries
        # (a single filing replaces search) or BASIC (the search market comes from the fetched fundamentals).
        search_task: Optional[asyncio.Future] = None
        search_finished = threading.Event()  # set in the worker thread; the task only notices once the loop runs
        if (
            search_decision.use_google_search
            and not user_url
            and data_requirement not in _FILING_REQUIREMENTS
            and data_requirement != FinancialDataRequirement.BASIC
        ):
            yield _searching_status(progress, routing.search_queries[0])
            search_task = asyncio.ensure_future(
                asyncio.to_thread(
                    _retrieve_routed,
                    routing,
                    market=resolve_market(None, question),
                    request_id=request_id,
                    ticker=ticker_norm.upper(),
                    company_name=ticker_norm.upper(),
                    company_name_lookup=lambda: self._company_name(ticker_norm),
                    finished=search_finished,
                )
            )
            search_task.add_done_callback(_consume_task_exception)
            await asyncio.sleep(0)  # start the Brave thread before the (sync) DB fetch blocks the loop

        period_wording = _period_wording(data_requirement, period_requirement)
        if period_wording and search_task is None:
            # With a search in flight the DB fetch starts at the same moment; the search status says more.
            yield progress.status(
                f"Loading {ticker_norm.upper()} {period_wording} financial reports...", phase=AnalysisPhase.DATA_FETCH
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
            yield progress.status(
                f"No {ticker_norm.upper()} financials found, writing your answer from our conversation...",
                phase=AnalysisPhase.ANALYZE,
            )
            company_name = company_fundamental.get("Name", "") if company_fundamental else ""
            async with aclosing(
                self._stream_fallback_answer(
                    question, ticker_norm, company_name, conversation_messages, preferred_model
                )
            ) as events:
                async for event in events:
                    yield event
            return

        has_financials = bool(company_fundamental) or bool(annual_statements) or bool(quarterly_statements)
        if has_financials and search_task is not None and not search_finished.is_set():
            # Only while Brave still runs; otherwise this would land together with the next status.
            yield progress.status(f"Analyzing {ticker_norm.upper()} financials...", phase=AnalysisPhase.ANALYZE)

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
        selected_passages: list[AnalyzePassage] = []
        is_url_grounded = False

        report_url: str | None = None
        if data_requirement == FinancialDataRequirement.QUARTERLY_SUMMARY and len(quarterly_statements) == 1:
            report_url = quarterly_statements[0].get("filing_10q_url")
        elif data_requirement == FinancialDataRequirement.ANNUAL_SUMMARY and len(annual_statements) == 1:
            report_url = annual_statements[0].get("filing_10k_url")

        if user_url:
            yield progress.status("Reading the attached document...", phase=AnalysisPhase.SEARCH)
            try:
                url_result = _ingest_user_url(user_url, question, request_id)
            except UrlIngestError:
                logger.exception("v2 URL ingest failed for user URL", extra={"request_id": request_id, "url": user_url})
                yield _url_ingest_error_event()
                return
            retrieved_sources = [url_result.source]
            selected_passages = url_result.selected_passages
            is_url_grounded = True
            sources_block = _build_sources_block(retrieved_sources, selected_passages, use_excerpt_labels=True)
        elif report_url:
            form = "10-Q" if data_requirement == FinancialDataRequirement.QUARTERLY_SUMMARY else "10-K"
            yield progress.status(f"Reading the {ticker_norm.upper()} {form} filing...", phase=AnalysisPhase.SEARCH)
            try:
                url_result = ingest_url(
                    url=report_url,
                    question=question,
                    request_id=request_id,
                    source_kind="filing",
                )
            except UrlIngestError:
                logger.exception("v2 URL ingest failed for report", extra={"request_id": request_id, "url": report_url})
                yield _url_ingest_error_event()
                return
            retrieved_sources = [url_result.source]
            selected_passages = url_result.selected_passages
            is_url_grounded = True
            sources_block = _build_sources_block(retrieved_sources, selected_passages, use_excerpt_labels=True)
        elif search_decision.use_google_search:
            if search_task is None:
                country = (company_fundamental or {}).get("Country") or (company_fundamental or {}).get("country")
                yield _searching_status(progress, routing.search_queries[0])
                retrieval_result = await asyncio.to_thread(
                    _retrieve_routed,
                    routing,
                    market=resolve_market(country, question),
                    request_id=request_id,
                    ticker=ticker_norm.upper(),
                    company_name=(company_fundamental or {}).get("Name") or ticker_norm.upper(),
                )
            else:
                retrieval_result = await search_task
            retrieved_sources = retrieval_result.sources
            selected_passages = retrieval_result.selected_passages
            sources_block = _build_sources_block(retrieved_sources, selected_passages)

        visual_prompt = PromptComponents.visual_output_instructions()
        temporal_context = build_temporal_context_block(question)
        url_grounding_directive = f"\n\n{_URL_GROUNDED_RULES}" if is_url_grounded else ""
        combined_prompt = (
            f"{financial_context}{conversation_context}{temporal_context}\n\n"
            f"{url_grounding_directive}"
            f"{visual_prompt}"
            f"{sources_block}"
            f"{_URL_FINAL_RESPONSE_FORMAT if is_url_grounded else ''}"
        )

        if debug_prompt_context:
            yield _build_prompt_debug_event(
                handler="company_specific_finance_v2",
                prompt=combined_prompt,
                retrieved_sources=retrieved_sources,
                selected_passages=selected_passages,
            )

        yield _writing_status(progress, [] if is_url_grounded else retrieved_sources)
        related_task = await start_related_questions(self._generate_related_questions(question))
        try:
            agent = MultiAgent(model_name=preferred_model)
            async with _answer_stream(
                _iter_answer_chunks(agent.generate_content(prompt=combined_prompt, use_google_search=False))
            ) as chunks:
                async for chunk in chunks:
                    yield {"type": "answer", "body": chunk}

            if retrieved_sources and not is_url_grounded:
                yield build_sources_event(retrieved_sources)

            yield {"type": "model_used", "body": agent.model_name}

            for related_q in await related_questions_result(related_task):
                yield related_q
        finally:
            related_task.cancel()
