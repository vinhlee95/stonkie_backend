"""Streaming chat about the signed-in user's portfolio.

Context (holdings, performance vs S&P 500, risk) is built server-side from the user's own data; the
client only sends the question and an optional holding to focus on. News-type questions add Brave
search results. Blocking work runs in threads (LLM and search on their own bounded pools) so one
chat never stalls the event loop, and a per-process cap sheds load instead of queueing forever.
"""

from __future__ import annotations

import asyncio
import contextvars
import datetime
import functools
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncGenerator, Literal

from langfuse import observe
from langfuse._client.get_client import get_client as get_langfuse_client

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.brave_client import BraveClient
from connectors.conversation_store import (
    append_assistant_message,
    append_user_message,
    generate_conversation_id,
    get_conversation_history_for_prompt,
)
from connectors.portfolio import PortfolioConnector
from connectors.yfinance_client import YFinanceClient
from services import rate_limit
from services.analysis_progress import AnalysisPhase, thinking_status
from services.analyze_retrieval.citation_index import build_sources_event
from services.analyze_retrieval.market import resolve_market
from services.analyze_retrieval.query_reformulator import QueryReformulator
from services.analyze_retrieval.retrieval import retrieve_for_analyze
from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeSource, BraveRetrievalError
from services.portfolio_chat_context import build_answer_prompt, build_sources_block, format_context
from services.portfolio_chat_targets import SearchMode, base_symbol, search_targets
from services.portfolio_snapshot import PortfolioUnavailableError, load_snapshot
from utils.answer_sanitizer import AnswerSanitizer
from utils.async_helpers import iterate_in_thread
from utils.chat_prompt import extract_answer_text, format_conversation
from utils.json_extract import extract_json_object

logger = logging.getLogger(__name__)

# Conversation-store namespace; can never collide with a ticker (tickers are [A-Z0-9.-=^]).
CONVERSATION_SCOPE = "__portfolio__"
# Routing is a small decision; a fixed fast model keeps it cheap whatever model the user picked.
CLASSIFIER_MODEL = ModelName.Gemini31FlashLite
# Blocking LLM and search calls get their own bounded pools, so a burst of chats queues there
# instead of starving the default executor every other to_thread caller uses.
CHAT_LLM_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="portfolio-chat-llm")
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
UNAVAILABLE_ERROR = {"type": "error", "code": "portfolio_unavailable", "body": "Couldn't load your portfolio"}

_in_flight = 0


class ScopeNotInPortfolioError(Exception):
    pass


class PortfolioChatStreamService:
    def __init__(
        self,
        portfolio: PortfolioConnector,
        yf_client: YFinanceClient,
        brave_client: BraveClient | None = None,
    ) -> None:
        self._portfolio = portfolio
        self._yf_client = yf_client
        self._brave_client = brave_client or BraveClient(api_key=os.getenv("BRAVE_API_KEY", ""))
        # Holdings already listed for this request (by resolve_scope), reused by stream.
        self._holdings: list | None = None

    async def allow_request(self, user_id: str) -> bool:
        """False when the user is over the per-minute chat limit."""
        return await asyncio.to_thread(rate_limit.allow, "portfolio_chat", user_id, RATE_LIMIT_PER_MINUTE, 60)

    async def resolve_scope(self, user_id: str, scope_ticker: str | None) -> str | None:
        """Normalised focus ticker, or None. Raises ScopeNotInPortfolioError for a ticker the user doesn't hold."""
        if not scope_ticker or not scope_ticker.strip():
            return None
        ticker = scope_ticker.strip().upper()
        self._holdings = await asyncio.to_thread(self._portfolio.list_holdings, user_id)
        if ticker not in {h.ticker for h in self._holdings}:
            raise ScopeNotInPortfolioError(ticker)
        return ticker

    @observe(name="portfolio_chat_classify")
    def _classify(self, *, question: str, tickers: list[str], conversation: str) -> ChatRoute:
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
            raw = "".join(
                chunk
                for chunk in agent.generate_content(prompt=prompt, use_google_search=False)
                if isinstance(chunk, str)
            )
            route = extract_json_object(raw).get("route")
            if route in ("portfolio_only", "needs_search", "unrelated"):
                return route
        except Exception:
            logger.exception("Portfolio chat classification failed; answering from portfolio data")
        return "portfolio_only"

    def _retrieve(self, **kwargs) -> tuple[list[AnalyzeSource], list[AnalyzePassage]]:
        try:
            result = retrieve_for_analyze(request_id=str(uuid.uuid4()), brave_client=self._brave_client, **kwargs)
        except BraveRetrievalError:
            logger.warning("Portfolio chat search found nothing for %s", kwargs.get("ticker"))
            return [], []
        return result.sources, result.selected_passages

    def _search_jobs(self, question: str, targets: list[dict], mode: SearchMode) -> list[dict]:
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

    async def _search(
        self, question: str, targets: list[dict], mode: SearchMode
    ) -> tuple[list[AnalyzeSource], list[AnalyzePassage]]:
        loop = asyncio.get_running_loop()
        results = await asyncio.gather(
            *(
                loop.run_in_executor(
                    CHAT_SEARCH_POOL, contextvars.copy_context().run, functools.partial(self._retrieve, **job)
                )
                for job in self._search_jobs(question, targets, mode)
            )
        )
        sources: list[AnalyzeSource] = []
        passages: list[AnalyzePassage] = []
        seen: set[str] = set()
        for found_sources, found_passages in results:
            for source in found_sources:
                if source.id not in seen:
                    seen.add(source.id)
                    sources.append(source)
            passages += found_passages
        return sources, passages

    @observe(
        name="portfolio_chat.stream",
        as_type="generation",
        capture_input=False,
        transform_to_string=extract_answer_text,
    )
    async def stream(
        self,
        *,
        user_id: str,
        question: str,
        scope_ticker: str | None,
        preferred_model: ModelName,
        conversation_id: str | None,
        is_disconnected,
    ) -> AsyncGenerator[dict[str, Any], None]:
        global _in_flight
        if _in_flight >= MAX_IN_FLIGHT:
            yield BUSY_ERROR
            return
        _in_flight += 1
        try:
            async for event in self._stream(
                user_id=user_id,
                question=question,
                scope_ticker=scope_ticker,
                preferred_model=preferred_model,
                conversation_id=conversation_id,
                is_disconnected=is_disconnected,
            ):
                yield event
        finally:
            _in_flight -= 1

    async def _stream(
        self,
        *,
        user_id: str,
        question: str,
        scope_ticker: str | None,
        preferred_model: ModelName,
        conversation_id: str | None,
        is_disconnected,
    ) -> AsyncGenerator[dict[str, Any], None]:
        langfuse = get_langfuse_client()
        if langfuse:
            langfuse.update_current_generation(input=question, metadata={"scope_ticker": scope_ticker})
        ttft_recorded = False

        conv_id = conversation_id or generate_conversation_id()
        history, _ = await asyncio.gather(
            asyncio.to_thread(get_conversation_history_for_prompt, user_id, CONVERSATION_SCOPE, conv_id),
            asyncio.to_thread(append_user_message, user_id, CONVERSATION_SCOPE, conv_id, question),
        )
        yield {"type": "conversation", "body": {"conversationId": conv_id}}

        yield thinking_status("Reading your portfolio…", phase=AnalysisPhase.ANALYZE, step=1, total_steps=3)
        conversation = format_conversation(history)
        holdings = self._holdings
        if holdings is None:
            try:
                holdings = await asyncio.to_thread(self._portfolio.list_holdings, user_id)
            except Exception:
                logger.exception("Portfolio chat could not list holdings")
                yield UNAVAILABLE_ERROR
                return
        # Routing needs only the question and tickers, so it runs while the snapshot loads.
        classify = asyncio.get_running_loop().run_in_executor(
            CHAT_LLM_POOL,
            contextvars.copy_context().run,
            functools.partial(
                self._classify, question=question, tickers=[h.ticker for h in holdings], conversation=conversation
            ),
        )
        try:
            snapshot = await load_snapshot(user_id, self._portfolio, self._yf_client, holdings=holdings)
        except PortfolioUnavailableError:
            logger.exception("Portfolio chat could not load the portfolio")
            yield UNAVAILABLE_ERROR
            return
        rows = snapshot.portfolio["holdings"]
        route = await classify
        if langfuse:
            langfuse.update_current_generation(metadata={"scope_ticker": scope_ticker, "route": route})
        if route == "unrelated":
            yield {"type": "answer", "body": UNRELATED_ANSWER}
            await asyncio.to_thread(append_assistant_message, user_id, CONVERSATION_SCOPE, conv_id, UNRELATED_ANSWER)
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
                sources, passages = await self._search(question, targets, mode)
                external_context = build_sources_block(sources, passages)
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
        async for chunk in iterate_in_thread(
            agent.generate_content(prompt=prompt, use_google_search=False), CHAT_LLM_POOL
        ):
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

        if sources:
            yield build_sources_event(sources)
        yield {"type": "model_used", "body": agent.model_name}
        if output:
            await asyncio.to_thread(append_assistant_message, user_id, CONVERSATION_SCOPE, conv_id, "".join(output))
