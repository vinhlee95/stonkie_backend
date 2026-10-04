"""Streaming chat about the signed-in user's portfolio.

Context (holdings, performance vs S&P 500, risk) is built server-side from the user's own data; the
client only sends the question and an optional holding to focus on. News-type questions add Brave
search results for the holdings involved. Blocking work (LLM, Redis, yfinance, Brave) runs in threads
so one chat never stalls the event loop.
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
from datetime import UTC
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
from services.analysis_progress import AnalysisPhase, thinking_status
from services.analyze_retrieval.citation_index import build_sources_event
from services.analyze_retrieval.market import resolve_market
from services.analyze_retrieval.query_reformulator import QueryReformulator
from services.analyze_retrieval.retrieval import retrieve_for_analyze
from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeSource, BraveRetrievalError
from services.portfolio import get_portfolio, get_quotes
from services.portfolio_chat_context import (
    PortfolioSnapshot,
    build_answer_prompt,
    build_sources_block,
    format_context,
)
from services.portfolio_chat_targets import base_symbol, search_targets
from services.portfolio_performance import EurSeries, load_eur_series, period_returns
from services.portfolio_risk import compute_risk
from utils.answer_sanitizer import AnswerSanitizer
from utils.async_helpers import iterate_in_thread
from utils.chat_prompt import extract_answer_text, format_conversation
from utils.json_extract import extract_json_object

logger = logging.getLogger(__name__)

# Conversation-store namespace; can never collide with a ticker (tickers are [A-Z0-9.-=^]).
CONVERSATION_SCOPE = "__portfolio__"
# Routing is a small decision; a fixed fast model keeps it cheap whatever model the user picked.
CLASSIFIER_MODEL = ModelName.Gemini31FlashLite
# Blocking LLM calls get their own bounded pool so a burst of chats queues here instead of
# starving the default executor every other to_thread caller uses.
CHAT_LLM_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="portfolio-chat-llm")

ChatRoute = Literal["portfolio_only", "needs_search", "unrelated"]

UNRELATED_ANSWER = (
    "This chat is about your portfolio. Ask me about your holdings, today's moves, "
    "performance against the S&P 500, risk and concentration, or news affecting what you own."
)


class PortfolioUnavailableError(Exception):
    pass


class ScopeNotInPortfolioError(Exception):
    pass


async def load_snapshot(
    user_id: str, portfolio: PortfolioConnector, yf_client: YFinanceClient, holdings: list | None = None
) -> PortfolioSnapshot:
    if holdings is None:
        holdings = await asyncio.to_thread(portfolio.list_holdings, user_id)
    try:
        # Fetched once and shared, so valuation and history don't both hit Yahoo on a cold cache.
        quotes = await asyncio.to_thread(get_quotes, [h.ticker for h in holdings], yf_client)
    except Exception as exc:
        raise PortfolioUnavailableError(user_id) from exc
    valued, series_result = await asyncio.gather(
        asyncio.to_thread(get_portfolio, user_id, portfolio, yf_client, quotes=quotes, holdings=holdings),
        asyncio.to_thread(_safe_series, holdings, yf_client, quotes),
        return_exceptions=True,
    )
    if isinstance(valued, BaseException):
        raise PortfolioUnavailableError(user_id) from valued
    series, excluded = (None, []) if isinstance(series_result, BaseException) else series_result
    today = datetime.datetime.now(UTC).date()
    risk, returns = await asyncio.to_thread(_risk_and_returns, series, valued["holdings"])
    return PortfolioSnapshot(portfolio=valued, returns=returns, risk=risk, today=today, excluded=excluded)


def _risk_and_returns(series: EurSeries | None, rows: list[dict]) -> tuple[dict | None, dict | None]:
    """pandas work, so it runs off the event loop; (None, None) if it fails."""
    try:
        return compute_risk(series, rows), (period_returns(series) if series is not None else None)
    except Exception:
        logger.exception("Portfolio chat risk/returns failed")
        return None, None


def _safe_series(holdings: list, yf_client: YFinanceClient, quotes: dict) -> tuple[EurSeries | None, list[str]]:
    """The EUR series and excluded tickers; (None, []) when loading failed (performance and
    beta/vol then show as unavailable while concentration still works)."""
    try:
        return load_eur_series(holdings, yf_client, quotes)
    except Exception:
        logger.exception("Portfolio chat price history failed")
        return None, []


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

    async def resolve_scope(self, user_id: str, scope_ticker: str | None) -> str | None:
        """Normalised focus ticker, or None. Raises ScopeNotInPortfolioError for a ticker the user doesn't hold."""
        if not scope_ticker or not scope_ticker.strip():
            return None
        ticker = scope_ticker.strip().upper()
        holdings = await asyncio.to_thread(self._portfolio.list_holdings, user_id)
        if ticker not in {h.ticker for h in holdings}:
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

    def _search_one(self, question: str, row: dict, named: bool) -> tuple[list[AnalyzeSource], list[AnalyzePassage]]:
        symbol = base_symbol(row["ticker"])
        name = row.get("name") or symbol
        when = f"on {row['trading_date']}" if row.get("trading_date") else "recently"
        try:
            result = retrieve_for_analyze(
                question=question if named else f"Why did {name} stock move {when}?",
                market=resolve_market(row.get("country"), question),
                request_id=str(uuid.uuid4()),
                brave_client=self._brave_client,
                ticker=symbol,
                company_name=name,
                query_reformulator=QueryReformulator() if named else None,
            )
        except BraveRetrievalError:
            logger.warning("Portfolio chat search found nothing for %s", row["ticker"])
            return [], []
        return result.sources, result.selected_passages

    def _search(
        self, question: str, targets: list[dict], named: bool
    ) -> tuple[list[AnalyzeSource], list[AnalyzePassage]]:
        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            results = list(pool.map(lambda row: self._search_one(question, row, named), targets))
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
        langfuse = get_langfuse_client()
        if langfuse:
            langfuse.update_current_generation(input=question, metadata={"scope_ticker": scope_ticker})
        ttft_recorded = False

        conv_id = conversation_id or generate_conversation_id()
        history = await asyncio.to_thread(get_conversation_history_for_prompt, user_id, CONVERSATION_SCOPE, conv_id)
        await asyncio.to_thread(append_user_message, user_id, CONVERSATION_SCOPE, conv_id, question)
        yield {"type": "conversation", "body": {"conversationId": conv_id}}

        yield thinking_status("Reading your portfolio…", phase=AnalysisPhase.ANALYZE, step=1, total_steps=3)
        conversation = format_conversation(history)
        try:
            holdings = await asyncio.to_thread(self._portfolio.list_holdings, user_id)
        except Exception:
            logger.exception("Portfolio chat could not list holdings")
            yield {"type": "error", "code": "portfolio_unavailable", "body": "Couldn't load your portfolio"}
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
            snapshot = await load_snapshot(user_id, self._portfolio, self._yf_client, holdings)
        except PortfolioUnavailableError:
            logger.exception("Portfolio chat could not load the portfolio")
            yield {"type": "error", "code": "portfolio_unavailable", "body": "Couldn't load your portfolio"}
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
            targets, named = search_targets(question, rows, scope_ticker)
            if targets:
                searched = True
                tickers = ", ".join(base_symbol(r["ticker"]) for r in targets)
                yield thinking_status(
                    f"Searching news for {tickers}…", phase=AnalysisPhase.SEARCH, step=2, total_steps=3
                )
                sources, passages = await asyncio.to_thread(self._search, question, targets, named)
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
