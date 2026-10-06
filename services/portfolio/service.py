"""PortfolioService: the only entry point for the portfolio router, and the only module in this package
that does I/O.

It constructs every connector/client (`x or XConnector()`), reads and writes the Redis cache, calls the
LLM, news search and conversation store, and owns the chat's thread pools and in-flight cap. Every other
module in this package is pure: it turns what is fetched here into results, and is never imported from
outside the package.
"""

import asyncio
import contextlib
import contextvars
import functools
import logging
import os
import uuid
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from langfuse import observe
from langfuse._client.get_client import get_client as get_langfuse_client

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors import cache
from connectors.brave_client import BraveClient
from connectors.company import CompanyClassificationDto, CompanyConnector
from connectors.conversation_store import (
    append_assistant_message,
    append_user_message,
    generate_conversation_id,
    get_conversation_history_for_prompt,
)
from connectors.fx import FxConnector
from connectors.portfolio import HoldingLimitExceeded, LotDto, LotLimitExceeded, PortfolioConnector
from connectors.yfinance_client import LiveQuoteDto, YFinanceClient
from services.analysis_progress import AnalysisPhase, thinking_status
from services.analyze_retrieval.citation_index import build_sources_event
from services.analyze_retrieval.query_reformulator import QueryReformulator
from services.analyze_retrieval.retrieval import retrieve_for_analyze
from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeSource, BraveRetrievalError
from services.portfolio import (
    chat,
    holding_metadata,
    live_quote,
    performance,
    price_history,
    rate_limit,
    valuation,
)
from services.portfolio.chat import ChatRoute, ChatScope, SearchJob
from services.portfolio.chat_prompt import build_answer_prompt, build_news_block, format_context
from services.portfolio.chat_targets import SearchMode, base_symbol, search_targets
from services.portfolio.errors import (
    HoldingLimitError,
    LotLimitError,
    PortfolioUnavailableError,
    QuoteUnavailableError,
    UnknownTickerError,
)
from services.portfolio.holding_metadata import HoldingMetadata
from services.portfolio.performance import EurSeries
from services.portfolio.snapshot import PortfolioSnapshot, risk_and_returns
from services.shared.price_change import PriceFetchError, get_price_change, get_price_changes
from utils.answer_sanitizer import AnswerSanitizer
from utils.async_helpers import iterate_in_thread
from utils.chat_prompt import format_conversation

logger = logging.getLogger(__name__)

# Blocking LLM and search calls get their own bounded pools, so a burst of chats queues there
# instead of starving the default executor every other to_thread caller uses.
CHAT_LLM_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="portfolio-chat-llm")
# Classification has its own pool: a stalled provider call that outlives its timeout keeps a worker
# busy, and must not leave answer streams queued behind it.
CHAT_CLASSIFIER_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="portfolio-chat-classify")
CHAT_SEARCH_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="portfolio-chat-search")
RATE_LIMIT_WINDOW_SECONDS = 60

_in_flight = 0


def _utcnow() -> datetime:
    return datetime.now(UTC)


@contextlib.asynccontextmanager
async def _in_flight_slot():
    """Yields False when chat.MAX_IN_FLIGHT chats are already running in this process."""
    global _in_flight
    if _in_flight >= chat.MAX_IN_FLIGHT:
        yield False
        return
    _in_flight += 1
    try:
        yield True
    finally:
        _in_flight -= 1


async def _best_effort(fn, *args):
    """Conversation history is optional context: a Redis outage must not stop the chat."""
    try:
        return await asyncio.to_thread(fn, *args)
    except Exception:
        logger.warning("Portfolio chat conversation store call %s failed", getattr(fn, "__name__", fn), exc_info=True)
        return None


def _lot_out(lot: LotDto) -> dict:
    return {"ticker": lot.ticker, **valuation.lot_to_dict(lot)}


class PortfolioService:
    def __init__(
        self,
        portfolio: PortfolioConnector | None = None,
        yf_client: YFinanceClient | None = None,
        brave_client: BraveClient | None = None,
        fx: FxConnector | None = None,
        companies: CompanyConnector | None = None,
    ) -> None:
        self._portfolio = portfolio or PortfolioConnector()
        self._yf_client = yf_client or YFinanceClient()
        # Only chat news search needs Brave; built on first use so other endpoints don't open an HTTP client.
        self._brave_client = brave_client
        self._fx = fx or FxConnector(self._yf_client)
        self._companies = companies or CompanyConnector()

    def _brave(self) -> BraveClient:
        if self._brave_client is None:
            self._brave_client = BraveClient(api_key=os.getenv("BRAVE_API_KEY", ""))
        return self._brave_client

    # --- Holdings -------------------------------------------------------------------------------

    def get_portfolio(self, user_id: str) -> dict:
        return self._value(self._portfolio.list_holdings(user_id))

    def get_performance(self, user_id: str) -> dict:
        series, excluded = self._eur_series(self._portfolio.list_holdings(user_id))
        return performance.performance_result(series, excluded)

    def add_lot(
        self, *, user_id: str, ticker: str, name: str | None, shares: float, price: float, purchased_on: date | None
    ) -> dict:
        """Raises the lot errors in services.portfolio.errors (unknown ticker, quote unavailable, limits)."""
        existing = self._portfolio.held_tickers(user_id)
        if ticker not in existing:
            # Fail fast before hitting Yahoo; the connector re-checks atomically on insert.
            if len(existing) >= valuation.MAX_HOLDINGS_PER_USER:
                raise HoldingLimitError(ticker)
            # Only new tickers are validated, so adding to a held position still works while Yahoo is down.
            self._resolve_quote(ticker)
        try:
            lot = self._portfolio.add_lot(
                user_id=user_id,
                ticker=ticker,
                name=name,
                shares=shares,
                price=price,
                purchased_on=purchased_on,
                max_holdings=valuation.MAX_HOLDINGS_PER_USER,
                max_lots=valuation.MAX_LOTS_PER_HOLDING,
            )
        except HoldingLimitExceeded:
            raise HoldingLimitError(ticker) from None
        except LotLimitExceeded:
            raise LotLimitError(ticker) from None
        return _lot_out(lot)

    def update_lot(self, *, user_id: str, lot_id: UUID, changes: dict) -> dict | None:
        lot = self._portfolio.update_lot(user_id=user_id, lot_id=lot_id, changes=changes)
        return _lot_out(lot) if lot is not None else None

    def remove_lot(self, *, user_id: str, lot_id: UUID) -> bool:
        return self._portfolio.delete_lot(user_id=user_id, lot_id=lot_id)

    def remove_holding(self, *, user_id: str, ticker: str) -> bool:
        return self._portfolio.delete_holding(user_id=user_id, ticker=ticker)

    def _resolve_quote(self, ticker: str) -> dict:
        """Latest quote for a ticker. Raises UnknownTickerError when Yahoo has no usable price and
        QuoteUnavailableError when the fetch itself fails."""
        try:
            quote = get_price_change(ticker, self._yf_client)
        except PriceFetchError:
            raise QuoteUnavailableError(ticker) from None
        if quote is None:
            raise UnknownTickerError(ticker)
        return quote

    def _value(self, holdings: list, quotes: dict[str, dict] | None = None) -> dict:
        """Valued holdings in EUR. `quotes` (from _quotes) can be passed in when already fetched."""
        tickers = [h.ticker for h in holdings]
        if quotes is None:
            quotes = self._quotes(tickers)
        metadata = self._holdings_metadata(tickers)
        fx_rates = {
            currency: self._fx.get_live_rate(currency, valuation.BASE_CURRENCY)
            for currency in valuation.fx_currencies(holdings, quotes)
        }
        return valuation.value_portfolio(holdings, quotes, metadata, fx_rates)

    # --- Quotes ---------------------------------------------------------------------------------

    def _quotes(self, tickers: list[str]) -> dict[str, dict]:
        """Live quote per ticker; tickers without one fall back to the last completed daily close."""
        if not tickers:
            return {}
        quotes = {t: valuation.live_to_quote(q) for t, q in self._live_quotes(tickers).items()}
        missing = [t for t in tickers if t not in quotes]
        if missing:
            for ticker, quote in get_price_changes(missing, self._yf_client).items():
                quotes[ticker] = valuation.delayed_quote(quote)
        return quotes

    def _live_quotes(self, tickers: list[str]) -> dict[str, LiveQuoteDto]:
        """Live quote per ticker, cached briefly. Tickers whose quote is unavailable or fails are omitted."""
        quotes: dict[str, LiveQuoteDto] = {}
        misses = []
        for ticker in tickers:
            cached = live_quote.from_cache(ticker, cache.get_json(live_quote.cache_key(ticker)))
            if cached is not None:
                quotes[ticker] = cached
            else:
                misses.append(ticker)
        if not misses:
            return quotes

        with ThreadPoolExecutor(max_workers=min(live_quote.MAX_WORKERS, len(misses))) as pool:
            fetched = pool.map(self._fetch_live_quote, misses)
        for ticker, quote in zip(misses, fetched):
            if quote is not None:
                quotes[ticker] = quote
                cache.set_json(
                    live_quote.cache_key(ticker), live_quote.to_json(quote), live_quote.LIVE_QUOTE_TTL_SECONDS
                )
        return quotes

    def _fetch_live_quote(self, ticker: str) -> LiveQuoteDto | None:
        try:
            quote = self._yf_client.get_live_quote(ticker)
        except Exception:
            logger.warning("Failed to fetch live quote for %s", ticker, exc_info=True)
            return None
        if quote is None:
            logger.info("No live quote for %s", ticker)
        return quote

    # --- Holding metadata -----------------------------------------------------------------------

    def _holdings_metadata(self, tickers: list[str]) -> dict[str, HoldingMetadata]:
        """Metadata for every ticker from cache, stored fundamentals, then Yahoo; unknown fields are
        "Other". Never raises."""
        result: dict[str, HoldingMetadata] = {}
        misses = []
        for ticker, cached in zip(tickers, cache.get_json_many([holding_metadata.cache_key(t) for t in tickers])):
            meta = holding_metadata.from_cache(cached)
            if meta is not None:
                result[ticker] = meta
            else:
                misses.append(ticker)
        if not misses:
            return result

        stored = self._stored_classifications(misses)
        fetch = []
        for ticker in misses:
            meta = holding_metadata.from_stored(stored.get(ticker))
            if meta is not None:
                self._store_metadata(result, ticker, meta, holding_metadata.METADATA_TTL_SECONDS)
            else:
                fetch.append(ticker)

        if fetch:
            with ThreadPoolExecutor(max_workers=min(holding_metadata.MAX_WORKERS, len(fetch))) as pool:
                infos = pool.map(self._yahoo_info, fetch)
            for ticker, info in zip(fetch, infos):
                meta = holding_metadata.from_info(ticker, info) if info is not None else None
                if meta is None:
                    self._store_metadata(
                        result, ticker, holding_metadata.unknown(), holding_metadata.FAILED_TTL_SECONDS
                    )
                else:
                    self._store_metadata(result, ticker, meta, holding_metadata.METADATA_TTL_SECONDS)
        return result

    def _stored_classifications(self, tickers: list[str]) -> dict[str, CompanyClassificationDto]:
        try:
            return self._companies.get_classifications(tickers)
        except Exception:
            logger.warning("Failed to read stored classifications", exc_info=True)
            return {}

    def _yahoo_info(self, ticker: str) -> dict | None:
        try:
            return self._yf_client.get_info(ticker)
        except Exception:
            logger.warning("Failed to fetch metadata for %s", ticker, exc_info=True)
            return None

    @staticmethod
    def _store_metadata(result: dict[str, HoldingMetadata], ticker: str, meta: HoldingMetadata, ttl: int) -> None:
        result[ticker] = meta
        cache.set_json(holding_metadata.cache_key(ticker), dict(meta), ttl)

    # --- Price history --------------------------------------------------------------------------

    def _close_histories(self, symbols: list[str]) -> dict[str, dict[str, float]]:
        """Daily closes per symbol as {ISO date: close}, oldest first. Cache misses are fetched in one
        batched download. Symbols without usable history (or a failed download) are omitted."""
        symbols = list(dict.fromkeys(symbols))
        today = _utcnow().date()
        histories: dict[str, dict[str, float]] = {}
        misses = []
        cached_entries = cache.get_json_many([price_history.cache_key(s, today) for s in symbols])
        for symbol, cached in zip(symbols, cached_entries):
            closes = price_history.from_cache(symbol, cached)
            if closes is None:
                misses.append(symbol)
            elif closes:
                histories[symbol] = closes
        if not misses:
            return histories

        try:
            batch = self._yf_client.get_close_history_batch(misses)
        except Exception:
            logger.warning("Failed to fetch price history for %s", misses, exc_info=True)
            return histories

        failed = set(batch.failed)
        for symbol in misses:
            if symbol in failed:
                continue  # transient: retried on the next request
            closes = price_history.completed_closes(batch.closes.get(symbol), today)
            key = price_history.cache_key(symbol, today)
            if not closes:
                logger.info("No price history for %s", symbol)
                cache.set_json(key, {"closes": {}}, price_history.NO_HISTORY_TTL_SECONDS)
                continue
            histories[symbol] = closes
            cache.set_json(key, {"closes": closes}, price_history.PRICE_HISTORY_TTL_SECONDS)
        return histories

    def _eur_series(self, holdings: list, quotes: dict[str, dict] | None = None) -> tuple[EurSeries | None, list[str]]:
        """EUR series for `holdings` plus the sorted tickers left out (see performance.build_eur_series).
        `quotes` (from _quotes) can be passed in when already fetched."""
        if not holdings:
            return None, []
        if quotes is None:
            quotes = self._quotes([h.ticker for h in holdings])
        positions, excluded = performance.positions_for(holdings, quotes)
        histories = self._close_histories(performance.history_symbols(positions))
        return performance.build_eur_series(positions, excluded, histories)

    def _safe_eur_series(self, holdings: list, quotes: dict[str, dict]) -> tuple[EurSeries | None, list[str]]:
        """(None, []) when loading failed: performance and beta/vol then show as unavailable while
        concentration still works."""
        try:
            return self._eur_series(holdings, quotes)
        except Exception:
            logger.exception("Portfolio chat price history failed")
            return None, []

    # --- Chat -----------------------------------------------------------------------------------

    async def allow_chat(self, user_id: str) -> bool:
        """False when the user is over the per-minute chat limit."""
        return await asyncio.to_thread(self._allow_chat, user_id)

    def _allow_chat(self, user_id: str) -> bool:
        key = rate_limit.window_key(chat.RATE_LIMIT_SCOPE, user_id, _utcnow().timestamp(), RATE_LIMIT_WINDOW_SECONDS)
        count = cache.incr_with_ttl(key, RATE_LIMIT_WINDOW_SECONDS)
        return rate_limit.within_limit(count, chat.RATE_LIMIT_PER_MINUTE)

    async def resolve_chat_scope(self, user_id: str, scope_ticker: str | None) -> ChatScope:
        """Lists the holdings once for the whole request. Raises ScopeNotInPortfolioError for a focus
        ticker the user doesn't hold, PortfolioUnavailableError when the holdings can't be listed."""
        try:
            holdings = await asyncio.to_thread(self._portfolio.list_holdings, user_id)
        except Exception as exc:
            raise PortfolioUnavailableError(user_id) from exc
        return chat.resolve_scope(holdings, scope_ticker)

    @observe(
        name="portfolio_chat.stream",
        as_type="generation",
        # Private: answers quote the user's holdings and values. Route/scope go in metadata instead.
        capture_input=False,
        capture_output=False,
    )
    async def stream_chat(
        self,
        *,
        user_id: str,
        question: str,
        scope: ChatScope,
        preferred_model: ModelName,
        conversation_id: str | None,
        is_disconnected,
    ) -> AsyncGenerator[dict[str, Any], None]:
        async with _in_flight_slot() as admitted:
            if not admitted:
                yield chat.BUSY_ERROR
                return
            try:
                async for event in self._answer_stream(
                    user_id=user_id,
                    question=question,
                    scope=scope,
                    preferred_model=preferred_model,
                    conversation_id=conversation_id,
                    is_disconnected=is_disconnected,
                ):
                    yield event
            except Exception:
                # Caught here, inside @observe: langfuse's wrapper swallows exceptions raised by async
                # generators, so the router would never see them and the client would get a silent cut-off.
                logger.exception("Portfolio chat stream failed")
                yield chat.INTERNAL_ERROR

    async def _answer_stream(
        self,
        *,
        user_id: str,
        question: str,
        scope: ChatScope,
        preferred_model: ModelName,
        conversation_id: str | None,
        is_disconnected,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """The chat events for one question. Errors propagate; stream_chat turns them into an error event."""
        scope_ticker = scope.ticker
        langfuse = get_langfuse_client()
        if langfuse:
            langfuse.update_current_generation(metadata={"scope_ticker": scope_ticker})
        ttft_recorded = False

        conv_id = conversation_id or generate_conversation_id()
        # Read before appending, so the history never already contains the current question.
        history = (
            await _best_effort(get_conversation_history_for_prompt, user_id, chat.CONVERSATION_SCOPE, conv_id) or []
        )
        await _best_effort(append_user_message, user_id, chat.CONVERSATION_SCOPE, conv_id, question)
        yield {"type": "conversation", "body": {"conversationId": conv_id}}

        yield thinking_status("Reading your portfolio…", phase=AnalysisPhase.ANALYZE, step=1, total_steps=3)
        conversation = format_conversation(history)
        holdings = scope.holdings
        # Routing needs only the question and tickers, so it runs while the snapshot loads.
        routing = asyncio.get_running_loop().run_in_executor(
            CHAT_CLASSIFIER_POOL,
            contextvars.copy_context().run,
            functools.partial(
                self._classify, question=question, tickers=[h.ticker for h in holdings], conversation=conversation
            ),
        )
        try:
            snapshot = await self._load_snapshot(user_id, list(holdings))
        except PortfolioUnavailableError:
            logger.exception("Portfolio chat could not load the portfolio")
            yield chat.UNAVAILABLE_ERROR
            return
        rows = snapshot.portfolio["holdings"]
        try:
            route = await asyncio.wait_for(routing, chat.CLASSIFIER_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("Portfolio chat classifier timed out; answering from portfolio data")
            route = chat.DEFAULT_ROUTE
        if langfuse:
            langfuse.update_current_generation(metadata={"scope_ticker": scope_ticker, "route": route})
        if route == "unrelated":
            yield {"type": "answer", "body": chat.UNRELATED_ANSWER}
            await _best_effort(
                append_assistant_message, user_id, chat.CONVERSATION_SCOPE, conv_id, chat.UNRELATED_ANSWER
            )
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
                sources, passages = await self._search_news(question, targets, mode)
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
                    langfuse.update_current_generation(completion_start_time=datetime.now())
                    ttft_recorded = True
                yield {"type": "answer", "body": text}
        tail = sanitizer.flush()
        if tail:
            output.append(tail)
            yield {"type": "answer", "body": tail}
        # The prompt asks for the disclaimer; enforce it in case the model skips it or is cut off.
        ending = chat.missing_disclaimer("".join(output))
        if ending:
            output.append(ending)
            yield {"type": "answer", "body": ending}

        if sources:
            yield build_sources_event(sources)
        yield {"type": "model_used", "body": agent.model_name}
        await _best_effort(append_assistant_message, user_id, chat.CONVERSATION_SCOPE, conv_id, "".join(output))

    # Inputs include conversation history with portfolio figures: trace the decision, not the inputs.
    @observe(name="portfolio_chat_classify", capture_input=False)
    def _classify(self, *, question: str, tickers: list[str], conversation: str) -> ChatRoute:
        prompt = chat.classifier_prompt(question=question, tickers=tickers, conversation=conversation)
        try:
            agent = MultiAgent(model_name=chat.CLASSIFIER_MODEL)
            raw = "".join(chunk for chunk in agent.generate_private_content(prompt=prompt) if isinstance(chunk, str))
        except Exception:
            logger.exception("Portfolio chat classification failed; answering from portfolio data")
            return chat.DEFAULT_ROUTE
        return chat.parse_route(raw)

    async def _load_snapshot(self, user_id: str, holdings: list) -> PortfolioSnapshot:
        """Everything the chat knows about the portfolio, loaded off the event loop. Raises
        PortfolioUnavailableError when the holdings can't be valued; performance and risk degrade to
        None on their own."""
        try:
            # Fetched once and shared, so valuation and history don't both hit Yahoo on a cold cache.
            quotes = await asyncio.to_thread(self._quotes, [h.ticker for h in holdings])
        except Exception as exc:
            raise PortfolioUnavailableError(user_id) from exc
        valued, series_result = await asyncio.gather(
            asyncio.to_thread(self._value, holdings, quotes),
            asyncio.to_thread(self._safe_eur_series, holdings, quotes),
            return_exceptions=True,
        )
        if isinstance(valued, BaseException):
            raise PortfolioUnavailableError(user_id) from valued
        series, excluded = (None, []) if isinstance(series_result, BaseException) else series_result
        # pandas work, so it runs off the event loop.
        risk, returns = await asyncio.to_thread(risk_and_returns, series, valued["holdings"])
        return PortfolioSnapshot(
            portfolio=valued, returns=returns, risk=risk, today=_utcnow().date(), excluded=excluded
        )

    async def _search_news(
        self, question: str, targets: list[dict], mode: SearchMode
    ) -> tuple[list[AnalyzeSource], list[AnalyzePassage]]:
        brave_client = self._brave()
        loop = asyncio.get_running_loop()
        results = await asyncio.gather(
            *(
                loop.run_in_executor(
                    CHAT_SEARCH_POOL,
                    contextvars.copy_context().run,
                    functools.partial(self._retrieve, brave_client, job),
                )
                for job in chat.search_jobs(question, targets, mode)
            )
        )
        return chat.merge_search_results(results)

    @staticmethod
    def _retrieve(brave_client: BraveClient, job: SearchJob) -> tuple[list[AnalyzeSource], list[AnalyzePassage]]:
        kwargs: dict[str, Any] = {}
        if job.ticker is not None:
            kwargs = dict(
                ticker=job.ticker,
                company_name=job.company_name,
                query_reformulator=QueryReformulator() if job.reformulate else None,
            )
        try:
            result = retrieve_for_analyze(
                request_id=str(uuid.uuid4()),
                brave_client=brave_client,
                question=job.question,
                market=job.market,
                **kwargs,
            )
        except BraveRetrievalError:
            logger.warning("Portfolio chat search found nothing for %s", job.ticker)
            return [], []
        return result.sources, result.selected_passages
