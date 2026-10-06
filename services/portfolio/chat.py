"""Portfolio chat: routing prompt, news-search jobs and answer rules. PortfolioService (service.py) runs
the chat (LLM, search, conversation store); everything here is pure.

Context (holdings, performance vs S&P 500, risk) is built server-side from the user's own data; the
client only sends the question and an optional holding to focus on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from ai_models.model_name import ModelName
from services.analyze_retrieval.market import resolve_market
from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeSource
from services.portfolio.chat_prompt import DISCLAIMER
from services.portfolio.chat_targets import SearchMode, base_symbol
from services.portfolio.errors import ScopeNotInPortfolioError
from utils.json_extract import extract_json_object

logger = logging.getLogger(__name__)

# Conversation-store namespace; can never collide with a ticker (tickers are [A-Z0-9.-=^]).
CONVERSATION_SCOPE = "__portfolio__"
# Routing is a small decision; a fixed fast model keeps it cheap whatever model the user picked.
CLASSIFIER_MODEL = ModelName.Gemini31FlashLite
# A slow provider must not hold the answer (and an in-flight slot) hostage to routing.
CLASSIFIER_TIMEOUT_SECONDS = 10
# Chats in flight per process; beyond this a request gets a "busy" error instead of stalling the rest.
MAX_IN_FLIGHT = 16
# Per user: each chat costs 2+ LLM calls and possibly Brave searches.
RATE_LIMIT_PER_MINUTE = 20
RATE_LIMIT_SCOPE = "portfolio_chat"

ChatRoute = Literal["portfolio_only", "needs_search", "unrelated"]
ROUTES = ("portfolio_only", "needs_search", "unrelated")
# Used when the classifier fails, times out or answers garbage.
DEFAULT_ROUTE: ChatRoute = "portfolio_only"

UNRELATED_ANSWER = (
    "This chat is about your portfolio. Ask me about your holdings, today's moves, "
    "performance against the S&P 500, risk and concentration, or news affecting what you own."
)
BUSY_ERROR = {"type": "error", "code": "busy", "body": "Portfolio chat is busy, please try again in a moment"}
INTERNAL_ERROR = {"type": "error", "code": "internal", "body": "Something went wrong"}
UNAVAILABLE_ERROR = {"type": "error", "code": "portfolio_unavailable", "body": "Couldn't load your portfolio"}


@dataclass(frozen=True)
class ChatScope:
    """One chat request's holdings (listed once) and its optional focus ticker."""

    holdings: tuple
    ticker: str | None = None


@dataclass(frozen=True)
class SearchJob:
    """One news retrieval. `ticker` None searches the question itself."""

    question: str
    market: str
    ticker: str | None = None
    company_name: str | None = None
    # Reformulate the user's own question into search queries (a holding named in it).
    reformulate: bool = False


def resolve_scope(holdings: list, scope_ticker: str | None) -> ChatScope:
    """Raises ScopeNotInPortfolioError for a focus ticker the user doesn't hold."""
    ticker = scope_ticker.strip().upper() if scope_ticker and scope_ticker.strip() else None
    if ticker is not None and ticker not in {h.ticker for h in holdings}:
        raise ScopeNotInPortfolioError(ticker)
    return ChatScope(holdings=tuple(holdings), ticker=ticker)


def classifier_prompt(*, question: str, tickers: list[str], conversation: str) -> str:
    return f"""
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


def parse_route(raw: str) -> ChatRoute:
    """The classifier's route; DEFAULT_ROUTE when its output isn't a known route."""
    try:
        route = extract_json_object(raw).get("route")
    except ValueError:
        logger.warning("Portfolio chat classifier answered without a route; answering from portfolio data")
        return DEFAULT_ROUTE
    return route if route in ROUTES else DEFAULT_ROUTE


def search_jobs(question: str, targets: list[dict], mode: SearchMode) -> list[SearchJob]:
    if mode == "question":
        return [SearchJob(question=question, market="GLOBAL")]
    jobs = []
    for row in targets:
        symbol = base_symbol(row["ticker"])
        name = row.get("name") or symbol
        when = f"on {row['trading_date']}" if row.get("trading_date") else "recently"
        named = mode == "named"
        jobs.append(
            SearchJob(
                question=question if named else f"Why did {name} stock move {when}?",
                market=resolve_market(row.get("country"), question),
                ticker=symbol,
                company_name=name,
                reformulate=named,
            )
        )
    return jobs


def merge_search_results(
    results: list[tuple[list[AnalyzeSource], list[AnalyzePassage]]],
) -> tuple[list[AnalyzeSource], list[AnalyzePassage]]:
    """All jobs' sources and passages, deduplicated by source id."""
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


def missing_disclaimer(answer: str) -> str | None:
    """The ending to append when the answer doesn't end with the disclaimer (the model skipped it or
    was cut off); None when it does."""
    return None if answer.rstrip().endswith(DISCLAIMER) else f"\n\n{DISCLAIMER}"
