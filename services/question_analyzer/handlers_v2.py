"""V2 question handlers for Brave-grounded analyze flow."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing
from typing import Any, AsyncGenerator, AsyncIterator, Callable, Dict, Iterable, Iterator, List, Optional

from agent.multi_agent import MultiAgent
from ai_models.chat_tool import ChatTool
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
from services.analyze_retrieval.schemas import AnalyzePassage, BraveRetrievalError
from services.analyze_retrieval.url_ingest import UrlIngestError, ingest_url
from services.question_analyzer.context_builders import ContextBuilderInput, get_context_builder
from services.question_analyzer.context_builders.components import PromptComponents
from services.question_analyzer.data_optimizer import FinancialDataOptimizer
from services.question_analyzer.router import RoutingResult
from services.question_analyzer.types import FinancialDataRequirement, FinancialPeriodRequirement
from services.question_analyzer.web_search_tool import WEB_SEARCH_TOOL_NAME, WebSearchTool
from services.question_analyzer.web_search_tool import build_sources_block as _build_sources_block
from services.search_decision_engine import SearchDecision
from services.shared.answer_stream import answer_stream
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
    retrieved_sources: list,
    search_decision: SearchDecision,
    *,
    is_url_grounded: bool = False,
    has_search_tool: bool = False,
) -> str:
    if is_url_grounded:
        return _URL_GROUNDED_RULES
    if retrieved_sources:
        return _GROUNDING_RULES
    if search_decision.reason_code == "stable_concept":
        return _STABLE_CONCEPT_ALLOW
    # A fixed decline would stop the model from using its web_search tool, or contradict what it finds.
    return PromptComponents.no_sources_search_first() if has_search_tool else _NO_DATA_DECLINE


def _iter_answer_chunks(chunks: Iterable[Any]) -> Iterator[str]:
    """Lazily yield text chunks so each one streams as soon as the LLM produces it.

    LLM generators yield ``str | dict`` (url_citation dicts); dicts are dropped.
    """
    for chunk in chunks:
        if isinstance(chunk, str):
            yield chunk


# Calculation questions (router `needs_calculation`) are answered with reasoning on: without it the fast model
# states a trend direction before computing it ("margin expanded from 69.0% to 67.2%"). Only when the client
# sends auto (the default) or fastest; an explicitly chosen model is used as is.
REASONING_ANSWER_MODEL = ModelName.Auto
REASONING_ANSWER_CONFIG = {"effort": "low"}
REASONING_STATUS = "Working through the numbers..."
# openrouter/auto candidate pool: fast, cheap models that stream readable reasoning. Exact ids: a
# "deepseek-v4.1-flash*" wildcard would also match the :batch variant.
REASONING_AUTO_ROUTER_MODELS = ("deepseek/deepseek-v4.1-flash-1017", "qwen/qwen3.7-flash", "z-ai/glm-5.3-flash")


# Answer model's web_search tool: rounds of extra searches per answer (comparison: per ticker).
MAX_TOOL_ROUNDS = 2
CHECKING_STATUS = "Checking what's behind the numbers..."
_STATUS_QUERY_CHARS = 80
_STATUS_PUBLISHERS = 3


def answer_call(
    preferred_model: ModelName,
    needs_calculation: bool,
    tools: Optional[List[ChatTool]] = None,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
) -> tuple[ModelName, dict[str, Any]]:
    """Model + generate_content options for a v2 answer call. Usage (served model, cost) is always recorded."""
    if needs_calculation and preferred_model in (ModelName.Auto, ModelName.Fastest):
        model, options = (
            REASONING_ANSWER_MODEL,
            {
                "reasoning": dict(REASONING_ANSWER_CONFIG),
                "auto_router_models": list(REASONING_AUTO_ROUTER_MODELS),
                "include_usage": True,
            },
        )
    else:
        model, options = preferred_model, {"include_usage": True}
    if tools:
        options["tools"] = list(tools)
        options["max_tool_rounds"] = max_tool_rounds
    return model, options


def _status_query(chunk: dict) -> str:
    query = " ".join(str((chunk.get("arguments") or {}).get("query") or "").split())
    return query if len(query) <= _STATUS_QUERY_CHARS else f"{query[:_STATUS_QUERY_CHARS]}…"


def tool_status_event(progress: ThinkingProgress, chunk: dict) -> Optional[dict]:
    """The thinking_status for a tool-loop dict from generate_content (tool_call / tool_result / budget), else None."""
    kind = chunk.get("type")
    if kind == "tool_budget_exhausted":
        return progress.status("Search limit reached, writing with what I found...", phase=AnalysisPhase.ANALYZE)
    if kind not in ("tool_call", "tool_result") or chunk.get("name") != WEB_SEARCH_TOOL_NAME:
        return None
    query = _status_query(chunk)
    if kind == "tool_call":
        return progress.status(f'Digging deeper: "{query}"...', phase=AnalysisPhase.SEARCH)
    meta = chunk.get("meta") or {}
    count = meta.get("source_count") or 0
    if not chunk.get("ok") or count <= 0:
        return progress.status(f'Nothing new on "{query}", continuing...', phase=AnalysisPhase.SEARCH)
    body = f'Found {count} source{"" if count == 1 else "s"} on "{query}"'
    publishers = list(meta.get("publishers") or [])[:_STATUS_PUBLISHERS]
    if publishers:
        body += f": {', '.join(publishers)}"
    return progress.status(body, phase=AnalysisPhase.SEARCH)


def iter_answer_events(
    chunks: Iterable[Any],
    writing_status: Optional[dict] | Callable[[], dict] = None,
    tool_status: Optional[Callable[[dict], Optional[dict]]] = None,
) -> Iterator[Dict[str, Any]]:
    """`reasoning` and `answer` events from an LLM stream, each as soon as the LLM produces it.

    Reasoning dicts become `reasoning` events only before the first answer text, so the event order stays
    reasoning* -> answer*. Tool-loop dicts become ``tool_status`` events, also only before the answer. Every other
    dict (url_citation) is dropped; no dict ever reaches an answer event. ``writing_status`` (a status, or a
    callable building it then, so it can count the tool rounds' sources) is yielded right before the first answer text.
    """
    answer_started = False
    for chunk in chunks:
        if isinstance(chunk, str):
            if not answer_started and writing_status:
                yield writing_status() if callable(writing_status) else writing_status
            answer_started = True
            yield {"type": "answer", "body": chunk}
        elif not answer_started and tool_status is not None and isinstance(chunk, dict):
            status = tool_status(chunk)
            if status is not None:
                yield status
                continue
            if chunk.get("type") == "reasoning" and isinstance(chunk.get("text"), str) and chunk["text"]:
                yield {"type": "reasoning", "body": chunk["text"]}
        elif (
            not answer_started
            and isinstance(chunk, dict)
            and chunk.get("type") == "reasoning"
            and isinstance(chunk.get("text"), str)
            and chunk["text"]
        ):
            yield {"type": "reasoning", "body": chunk["text"]}


def answer_events(
    chunks: Iterable[Any],
    answer_options: dict[str, Any],
    progress: ThinkingProgress,
    writing_status: Callable[[], dict],
) -> Iterator[Dict[str, Any]]:
    """The answer call's events in order, statuses included; `answer_options` comes from `answer_call`.

    Reasoning off: the writing status, then the answer. Reasoning on: a status that holds while the model reasons
    (readable reasoning may never stream), the reasoning, then the writing status right before the answer text.
    """
    if "tools" in answer_options:
        # Tool rounds run before any answer text: hold a status, report each search, write status last so it
        # counts the sources the searches added.
        holding = REASONING_STATUS if "reasoning" in answer_options else CHECKING_STATUS
        yield progress.status(holding, phase=AnalysisPhase.ANALYZE)
        yield from iter_answer_events(chunks, writing_status, lambda chunk: tool_status_event(progress, chunk))
        return
    if "reasoning" not in answer_options:
        yield writing_status()
        yield from iter_answer_events(chunks)
        return
    yield progress.status(REASONING_STATUS, phase=AnalysisPhase.ANALYZE)
    yield from iter_answer_events(chunks, writing_status())


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


NO_WEB_RESULTS_STATUS = "No relevant web results, answering from the available data..."
# Old fixed-length format, kept for URL-grounded answers (no narrative search there).
_URL_ANSWER_FORMAT = {
    "company_general": "Keep response concise under 200 words.\nUse short paragraphs and bullet points for readability.",
    "general_finance": "Keep the answer under 150 words. Break into short paragraphs.",
}


def _search_label(routing: RoutingResult) -> str:
    return routing.search_queries[0] if routing.search_queries else routing.standalone_question


def _narrative_instructions() -> str:
    return f"{PromptComponents.narrative_answer_format()}\n\n{PromptComponents.search_instruction()}"


def _web_search_tool(
    *,
    market,
    request_id: str,
    ticker: str | None,
    company_name: str | None,
    known_sources: list,
) -> WebSearchTool:
    """The answer model's web_search tool: one Brave retrieval per query, same relevance filter as the prompt's."""
    brave_client = BraveClient(api_key=os.getenv("BRAVE_API_KEY", ""))

    def search(query: str):
        return retrieve_for_analyze(
            question=query,
            market=market,
            request_id=request_id,
            brave_client=brave_client,
            ticker=ticker,
            company_name=company_name,
            query_reformulator=PrecomputedQueryReformulator([query]),
        )

    return WebSearchTool(search, known_sources=known_sources)


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
        company = await asyncio.to_thread(self.company_connector.get_by_ticker, ticker)
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
        market = resolve_market(getattr(company, "country", None), question)
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
        else:
            yield _searching_status(progress, _search_label(routing))
            try:
                retrieval_result = await asyncio.to_thread(
                    _retrieve_routed,
                    routing,
                    market=market,
                    request_id=request_id,
                    ticker=ticker.upper(),
                    company_name=company_name,
                )
                retrieved_sources = retrieval_result.sources
                selected_passages = retrieval_result.selected_passages
                sources_context = _build_sources_block(retrieved_sources, selected_passages)
            except BraveRetrievalError:
                logger.info("v2 initial search found nothing relevant", extra={"request_id": request_id})
                yield progress.status(NO_WEB_RESULTS_STATUS, phase=AnalysisPhase.SEARCH)

        grounding_directive = _select_grounding(
            retrieved_sources,
            search_decision,
            is_url_grounded=is_url_grounded,
            has_search_tool=not is_url_grounded,
        )

        temporal_context = build_temporal_context_block(question)
        prompt = f"""
You are an expert about a business.
Answer this question about {company_name} (ticker: {ticker}):
{question}
{temporal_context}
{grounding_directive}

IMPORTANT: Always respond in same language as the current question.
{_URL_ANSWER_FORMAT["company_general"] if is_url_grounded else _narrative_instructions()}
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

        web_search = _web_search_tool(
            market=market,
            request_id=request_id,
            ticker=ticker.upper(),
            company_name=company_name,
            known_sources=retrieved_sources,
        )
        answer_model, answer_options = answer_call(
            preferred_model, routing.needs_calculation, tools=None if is_url_grounded else [web_search.chat_tool()]
        )
        related_task = await start_related_questions(self._generate_related_questions(question))
        try:
            agent = MultiAgent(model_name=answer_model)
            async with answer_stream(
                answer_events(
                    agent.generate_content(prompt=prompt, use_google_search=False, **answer_options),
                    answer_options,
                    progress,
                    lambda: _writing_status(
                        progress, [] if is_url_grounded else web_search.merged_sources(retrieved_sources, [])[0]
                    ),
                )
            ) as events:
                async for event in events:
                    yield event

            footer_sources = web_search.merged_sources(retrieved_sources, selected_passages)[0]
            if footer_sources and not is_url_grounded:
                yield build_sources_event(footer_sources)

            yield {"type": "model_used", "body": agent.served_model_name}

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
        else:
            yield _searching_status(progress, _search_label(routing))
            try:
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
            except BraveRetrievalError:
                logger.info("v2 initial search found nothing relevant", extra={"request_id": request_id})
                yield progress.status(NO_WEB_RESULTS_STATUS, phase=AnalysisPhase.SEARCH)

        grounding_directive = _select_grounding(
            retrieved_sources,
            search_decision,
            is_url_grounded=is_url_grounded,
            has_search_tool=not is_url_grounded,
        )

        temporal_context = build_temporal_context_block(question)
        prompt = f"""
Please explain this financial concept or answer this question:
{question}
{temporal_context}
{grounding_directive}

IMPORTANT: Always respond in the same language as the current question.
{_URL_ANSWER_FORMAT["general_finance"] if is_url_grounded else _narrative_instructions()}
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

        web_search = _web_search_tool(
            market=resolve_market(None, question),
            request_id=request_id,
            ticker=None,
            company_name=None,
            known_sources=retrieved_sources,
        )
        answer_model, answer_options = answer_call(
            preferred_model, routing.needs_calculation, tools=None if is_url_grounded else [web_search.chat_tool()]
        )
        related_task = await start_related_questions(self._generate_related_questions(question))
        try:
            agent = MultiAgent(model_name=answer_model)
            async with answer_stream(
                answer_events(
                    agent.generate_content(prompt=prompt, use_google_search=False, **answer_options),
                    answer_options,
                    progress,
                    lambda: _writing_status(
                        progress, [] if is_url_grounded else web_search.merged_sources(retrieved_sources, [])[0]
                    ),
                )
            ) as events:
                async for event in events:
                    yield event

            footer_sources = web_search.merged_sources(retrieved_sources, selected_passages)[0]
            if footer_sources and not is_url_grounded:
                yield build_sources_event(footer_sources)

            yield {"type": "model_used", "body": agent.served_model_name}

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
            async with answer_stream(
                _iter_answer_chunks(agent.generate_content(prompt=prompt, use_google_search=False))
            ) as chunks:
                async for chunk in chunks:
                    yield {"type": "answer", "body": chunk}
            yield {"type": "model_used", "body": agent.served_model_name}

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
            not user_url
            and data_requirement not in _FILING_REQUIREMENTS
            and data_requirement != FinancialDataRequirement.BASIC
        ):
            yield _searching_status(progress, _search_label(routing))
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

        country = (company_fundamental or {}).get("Country") or (company_fundamental or {}).get("country")
        market = resolve_market(country, question)
        company_name = (company_fundamental or {}).get("Name") or ticker_norm.upper()

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
        else:
            try:
                if search_task is None:
                    yield _searching_status(progress, _search_label(routing))
                    retrieval_result = await asyncio.to_thread(
                        _retrieve_routed,
                        routing,
                        market=market,
                        request_id=request_id,
                        ticker=ticker_norm.upper(),
                        company_name=company_name,
                    )
                else:
                    retrieval_result = await search_task
                retrieved_sources = retrieval_result.sources
                selected_passages = retrieval_result.selected_passages
                sources_block = _build_sources_block(retrieved_sources, selected_passages)
            except BraveRetrievalError:
                logger.info("v2 initial search found nothing relevant", extra={"request_id": request_id})
                yield progress.status(
                    f"No relevant web results, analyzing {ticker_norm.upper()} financials...",
                    phase=AnalysisPhase.SEARCH,
                )

        visual_prompt = PromptComponents.visual_output_instructions()
        temporal_context = build_temporal_context_block(question)
        url_grounding_directive = f"\n\n{_URL_GROUNDED_RULES}" if is_url_grounded else ""
        search_directive = "" if is_url_grounded else f"\n\n{PromptComponents.search_instruction()}"
        combined_prompt = (
            f"{financial_context}{conversation_context}{temporal_context}\n\n"
            f"{url_grounding_directive}"
            f"{visual_prompt}"
            f"{sources_block}"
            f"{_URL_FINAL_RESPONSE_FORMAT if is_url_grounded else ''}"
            f"{search_directive}"
        )

        if debug_prompt_context:
            yield _build_prompt_debug_event(
                handler="company_specific_finance_v2",
                prompt=combined_prompt,
                retrieved_sources=retrieved_sources,
                selected_passages=selected_passages,
            )

        web_search = _web_search_tool(
            market=market,
            request_id=request_id,
            ticker=ticker_norm.upper(),
            company_name=company_name,
            known_sources=retrieved_sources,
        )
        answer_model, answer_options = answer_call(
            preferred_model, routing.needs_calculation, tools=None if is_url_grounded else [web_search.chat_tool()]
        )
        related_task = await start_related_questions(self._generate_related_questions(question))
        try:
            agent = MultiAgent(model_name=answer_model)
            async with answer_stream(
                answer_events(
                    agent.generate_content(prompt=combined_prompt, use_google_search=False, **answer_options),
                    answer_options,
                    progress,
                    lambda: _writing_status(
                        progress, [] if is_url_grounded else web_search.merged_sources(retrieved_sources, [])[0]
                    ),
                )
            ) as events:
                async for event in events:
                    yield event

            footer_sources = web_search.merged_sources(retrieved_sources, selected_passages)[0]
            if footer_sources and not is_url_grounded:
                yield build_sources_event(footer_sources)

            yield {"type": "model_used", "body": agent.served_model_name}

            for related_q in await related_questions_result(related_task):
                yield related_q
        finally:
            related_task.cancel()
