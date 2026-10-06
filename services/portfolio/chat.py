"""Portfolio chat helpers: routing, news search and the answer stream. PortfolioService (service.py)
is the entry point and passes in every connector; nothing here constructs one.

Context (holdings, performance vs S&P 500, risk) is built server-side from the user's own data; the
client only sends the question and an optional holding to focus on. Blocking work runs on bounded
thread pools so one chat never stalls the event loop, and a per-process cap sheds load.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import datetime
import functools
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Literal

from langfuse import observe
from langfuse._client.get_client import get_client as get_langfuse_client

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.brave_client import BraveClient
from connectors.company import CompanyConnector
from connectors.conversation_store import (
    append_assistant_message,
    append_user_message,
    generate_conversation_id,
    get_conversation_history_for_prompt,
)
from connectors.fx import FxConnector
from connectors.portfolio import PortfolioConnector
from connectors.yfinance_client import YFinanceClient
from services.analysis_progress import AnalysisPhase, thinking_status
from services.analyze_retrieval.citation_index import build_sources_event
from services.analyze_retrieval.market import resolve_market
from services.analyze_retrieval.query_reformulator import QueryReformulator
from services.analyze_retrieval.retrieval import retrieve_for_analyze
from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeSource, BraveRetrievalError
from services.portfolio import rate_limit
from services.portfolio.chat_prompt import DISCLAIMER, build_answer_prompt, build_news_block, format_context
from services.portfolio.chat_targets import SearchMode, base_symbol, search_targets
from services.portfolio.errors import PortfolioUnavailableError, ScopeNotInPortfolioError
from services.portfolio.snapshot import load_snapshot
from utils.answer_sanitizer import AnswerSanitizer
from utils.async_helpers import iterate_in_thread
from utils.chat_prompt import format_conversation
from utils.json_extract import extract_json_object

logger = logging.getLogger(__name__)

# Conversation-store namespace; can never collide with a ticker (tickers are [A-Z0-9.-=^]).
CONVERSATION_SCOPE = "__portfolio__"
# Routing is a small decision; a fixed fast model keeps it cheap whatever model the user picked.
CLASSIFIER_MODEL = ModelName.Gemini31FlashLite
# A slow provider must not hold the answer (and an in-flight slot) hostage to routing.
CLASSIFIER_TIMEOUT_SECONDS = 10
# Blocking LLM and search calls get their own bounded pools, so a burst of chats queues there
# instead of starving the default executor every other to_thread caller uses.
CHAT_LLM_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="portfolio-chat-llm")
# Classification has its own pool: a stalled provider call that outlives its timeout keeps a worker
# busy, and must not leave answer streams queued behind it.
CHAT_CLASSIFIER_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="portfolio-chat-classify")
CHAT_SEARCH_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="portfolio-chat-search")
# Chats in flight per process; beyond this a request gets a "busy" error instead of stalling the rest.
MAX_IN_FLIGHT = 16
# Per user: each chat costs 2+ LLM calls and possibly Brave searches.
RATE_LIMIT_PER_MINUTE = 20

ChatRoute = Literal["portfolio_only", "needs_search", "unrelated"]

UNRELATED_ANSWER = (
    "This chat is about your portfolio. Ask me about your holdings, today's moves, "
    "performance against the S&P 500, risk and concentration, or news affecting what you own."
)
BUSY_ERROR = {"type": "error", "code": "busy", "body": "Portfolio chat is busy, please try again in a moment"}
INTERNAL_ERROR = {"type": "error", "code": "internal", "body": "Something went wrong"}
UNAVAILABLE_ERROR = {"type": "error", "code": "portfolio_unavailable", "body": "Couldn't load your portfolio"}

_in_flight = 0


@dataclass(frozen=True)
class ChatScope:
    """One chat request's holdings (listed once) and its optional focus ticker."""

    holdings: tuple
    ticker: str | None = None


@contextlib.asynccontextmanager
async def in_flight_slot():
    """Yields False when MAX_IN_FLIGHT chats are already running in this process."""
    global _in_flight
    if _in_flight >= MAX_IN_FLIGHT:
        yield False
        return
    _in_flight += 1
    try:
        yield True
    finally:
        _in_flight -= 1


def allow_request(user_id: str) -> bool:
    """False when the user is over the per-minute chat limit."""
    return rate_limit.allow("portfolio_chat", user_id, RATE_LIMIT_PER_MINUTE, 60)


def resolve_scope(holdings: list, scope_ticker: str | None) -> ChatScope:
    """Raises ScopeNotInPortfolioError for a focus ticker the user doesn't hold."""
    ticker = scope_ticker.strip().upper() if scope_ticker and scope_ticker.strip() else None
    if ticker is not None and ticker not in {h.ticker for h in holdings}:
        raise ScopeNotInPortfolioError(ticker)
    return ChatScope(holdings=tuple(holdings), ticker=ticker)


async def _best_effort(fn, *args):
    """Conversation history is optional context: a Redis outage must not stop the chat."""
    try:
        return await asyncio.to_thread(fn, *args)
    except Exception:
        logger.warning("Portfolio chat conversation store call %s failed", getattr(fn, "__name__", fn), exc_info=True)
        return None


# Inputs include conversation history with portfolio figures: trace the decision, not the inputs.
@observe(name="portfolio_chat_classify", capture_input=False)
def classify(*, question: str, tickers: list[str], conversation: str) -> ChatRoute:
    prompt = f"""
You are a strict JSON classifier for a chat about the user's stock portfolio.

Holdings: {", ".join(tickers) or "none"}

Classify the user's current question into exactly one route:
- portfolio_only: answerable from portfolio data alone (weights, values, P/L, latest % moves, performance vs S&P 500, beta, volatility, drawdown, sector/country concentration).
- needs_search: needs current outside information, e.g. why a holding or the portfolio moved, news, earnings, company events, macro or market drivers.
- unrelated: not about the portfolio, markets, finance, companies or investing.

Current question:
{question}

{conversation}

Output ONLY JSON:
{{"route":"portfolio_only|needs_search|unrelated","reason":"short reason"}}
    """.strip()
    try:
        agent = MultiAgent(model_name=CLASSIFIER_MODEL)
        raw = "".join(chunk for chunk in agent.generate_private_content(prompt=prompt) if isinstance(chunk, str))
        route = extract_json_object(raw).get("route")
        if route in ("portfolio_only", "needs_search", "unrelated"):
            return route
    except Exception:
        logger.exception("Portfolio chat classification failed; answering from portfolio data")
    return "portfolio_only"


def _retrieve(brave_client: BraveClient, **kwargs) -> tuple[list[AnalyzeSource], list[AnalyzePassage]]:
    try:
        result = retrieve_for_analyze(request_id=str(uuid.uuid4()), brave_client=brave_client, **kwargs)
    except BraveRetrievalError:
        logger.warning("Portfolio chat search found nothing for %s", kwargs.get("ticker"))
        return [], []
    return result.sources, result.selected_passages


def _search_jobs(question: str, targets: list[dict], mode: SearchMode) -> list[dict]:
    if mode == "question":
        return [dict(question=question, market="GLOBAL")]
    jobs = []
    for row in targets:
        symbol = base_symbol(row["ticker"])
        name = row.get("name") or symbol
        when = f"on {row['trading_date']}" if row.get("trading_date") else "recently"
        named = mode == "named"
        jobs.append(
            dict(
                question=question if named else f"Why did {name} stock move {when}?",
                market=resolve_market(row.get("country"), question),
                ticker=symbol,
                company_name=name,
                query_reformulator=QueryReformulator() if named else None,
            )
        )
    return jobs


async def search_news(
    brave_client: BraveClient, question: str, targets: list[dict], mode: SearchMode
) -> tuple[list[AnalyzeSource], list[AnalyzePassage]]:
    loop = asyncio.get_running_loop()
    results = await asyncio.gather(
        *(
            loop.run_in_executor(
                CHAT_SEARCH_POOL, contextvars.copy_context().run, functools.partial(_retrieve, brave_client, **job)
            )
            for job in _search_jobs(question, targets, mode)
        )
    )
    sources: list[AnalyzeSource] = []
    passages: list[AnalyzePassage] = []
    seen: set[str] = set()
    for found_sources, found_passages in results:
        # A source found for two holdings keeps only the first retrieval's passages.
        new_ids = {s.id for s in found_sources} - seen
        for source in found_sources:
            if source.id in new_ids:
                seen.add(source.id)
                sources.append(source)
        passages += [p for p in found_passages if p.source_id in new_ids]
    return sources, passages


async def answer_stream(
    *,
    portfolio: PortfolioConnector,
    yf_client: YFinanceClient,
    fx: FxConnector,
    companies: CompanyConnector,
    brave_client: BraveClient,
    user_id: str,
    question: str,
    scope: ChatScope,
    preferred_model: ModelName,
    conversation_id: str | None,
    is_disconnected,
) -> AsyncGenerator[dict[str, Any], None]:
    """The chat events for one question. Errors propagate; the service turns them into an error event."""
    scope_ticker = scope.ticker
    langfuse = get_langfuse_client()
    if langfuse:
        langfuse.update_current_generation(metadata={"scope_ticker": scope_ticker})
    ttft_recorded = False

    conv_id = conversation_id or generate_conversation_id()
    # Read before appending, so the history never already contains the current question.
    history = await _best_effort(get_conversation_history_for_prompt, user_id, CONVERSATION_SCOPE, conv_id) or []
    await _best_effort(append_user_message, user_id, CONVERSATION_SCOPE, conv_id, question)
    yield {"type": "conversation", "body": {"conversationId": conv_id}}

    yield thinking_status("Reading your portfolio…", phase=AnalysisPhase.ANALYZE, step=1, total_steps=3)
    conversation = format_conversation(history)
    holdings = scope.holdings
    # Routing needs only the question and tickers, so it runs while the snapshot loads.
    routing = asyncio.get_running_loop().run_in_executor(
        CHAT_CLASSIFIER_POOL,
        contextvars.copy_context().run,
        functools.partial(classify, question=question, tickers=[h.ticker for h in holdings], conversation=conversation),
    )
    try:
        snapshot = await load_snapshot(
            user_id, portfolio=portfolio, yf_client=yf_client, fx=fx, companies=companies, holdings=list(holdings)
        )
    except PortfolioUnavailableError:
        logger.exception("Portfolio chat could not load the portfolio")
        yield UNAVAILABLE_ERROR
        return
    rows = snapshot.portfolio["holdings"]
    try:
        route = await asyncio.wait_for(routing, CLASSIFIER_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning("Portfolio chat classifier timed out; answering from portfolio data")
        route = "portfolio_only"
    if langfuse:
        langfuse.update_current_generation(metadata={"scope_ticker": scope_ticker, "route": route})
    if route == "unrelated":
        yield {"type": "answer", "body": UNRELATED_ANSWER}
        await _best_effort(append_assistant_message, user_id, CONVERSATION_SCOPE, conv_id, UNRELATED_ANSWER)
        return

    sources: list[AnalyzeSource] = []
    external_context = ""
    searched = False
    if route == "needs_search":
        targets, mode = search_targets(question, rows, scope_ticker)
        if targets or mode == "question":
            searched = True
            about = ", ".join(base_symbol(r["ticker"]) for r in targets) or "your question"
            yield thinking_status(f"Searching news for {about}…", phase=AnalysisPhase.SEARCH, step=2, total_steps=3)
            sources, passages = await search_news(brave_client, question, targets, mode)
            external_context = build_news_block(sources, passages)
    if await is_disconnected():
        return

    prompt = build_answer_prompt(
        question=question,
        context=format_context(snapshot),
        conversation=conversation,
        scope_ticker=scope_ticker,
        searched=searched,
        external_context=external_context,
    )
    yield thinking_status("Writing your answer…", phase=AnalysisPhase.ANALYZE, step=3, total_steps=3)
    # Plain, sanitized answer events only: the prompt mixes private data with web text, so no
    # visual (HTML/SVG) blocks, links, images or URLs that could carry data out.
    output: list[str] = []
    sanitizer = AnswerSanitizer()
    agent = MultiAgent(model_name=preferred_model)
    # aclosing: returning on disconnect closes the LLM stream now, not at garbage collection.
    async with contextlib.aclosing(
        iterate_in_thread(agent.generate_private_content(prompt=prompt), CHAT_LLM_POOL)
    ) as chunks:
        async for chunk in chunks:
            if await is_disconnected():
                return
            if not isinstance(chunk, str):
                continue
            text = sanitizer.feed(chunk)
            if not text:
                continue
            output.append(text)
            if not ttft_recorded and langfuse:
                langfuse.update_current_generation(completion_start_time=datetime.datetime.now())
                ttft_recorded = True
            yield {"type": "answer", "body": text}
    tail = sanitizer.flush()
    if tail:
        output.append(tail)
        yield {"type": "answer", "body": tail}
    # The prompt asks for the disclaimer; enforce it in case the model skips it or is cut off.
    if not "".join(output).rstrip().endswith(DISCLAIMER):
        ending = f"\n\n{DISCLAIMER}"
        output.append(ending)
        yield {"type": "answer", "body": ending}

    if sources:
        yield build_sources_event(sources)
    yield {"type": "model_used", "body": agent.model_name}
    await _best_effort(append_assistant_message, user_id, CONVERSATION_SCOPE, conv_id, "".join(output))
