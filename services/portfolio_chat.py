"""Streaming chat about the signed-in user's portfolio.

Context (holdings, performance vs S&P 500, risk) is built server-side from the user's own data; the
client only sends the question and an optional holding to focus on. News-type questions add Brave
search results for the holdings involved.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Literal

import pandas as pd
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
from services.portfolio import get_portfolio
from services.portfolio_performance import EurSeries, load_eur_series
from services.portfolio_risk import compute_risk
from utils.visual_stream import VisualAnswerStreamSplitter

logger = logging.getLogger(__name__)

CONVERSATION_SCOPE = "portfolio"
# Holdings searched per question: the focus holding(s), or today's biggest movers.
MAX_SEARCH_HOLDINGS = 2
DISCLAIMER = "Not financial advice."

ChatRoute = Literal["portfolio_only", "needs_search", "unrelated"]

UNRELATED_ANSWER = (
    "This chat is about your portfolio. Ask me about your holdings, today's moves, "
    "performance against the S&P 500, risk and concentration, or news affecting what you own."
)


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Everything the chat knows about the portfolio. `returns` / `risk` are None when unavailable."""

    portfolio: dict
    returns: dict | None
    risk: dict | None


class PortfolioUnavailableError(Exception):
    pass


async def load_snapshot(user_id: str, portfolio: PortfolioConnector, yf_client: YFinanceClient) -> PortfolioSnapshot:
    holdings = await asyncio.to_thread(portfolio.list_holdings, user_id)
    valued, series = await asyncio.gather(
        asyncio.to_thread(get_portfolio, user_id, portfolio, yf_client),
        asyncio.to_thread(_safe_series, holdings, yf_client),
        return_exceptions=True,
    )
    if isinstance(valued, BaseException):
        raise PortfolioUnavailableError(user_id) from valued
    if isinstance(series, BaseException):
        series = None
    try:
        risk = compute_risk(series, valued["holdings"])
        returns = period_returns(series) if series is not None else None
    except Exception:
        logger.exception("Portfolio chat risk/returns failed")
        risk, returns = None, None
    return PortfolioSnapshot(portfolio=valued, returns=returns, risk=risk)


def _safe_series(holdings: list, yf_client: YFinanceClient) -> EurSeries | None:
    """The EUR series; None when nothing can be priced or loading failed (performance and beta/vol
    then show as unavailable while concentration still works)."""
    try:
        series, _ = load_eur_series(holdings, yf_client)
        return series
    except Exception:
        logger.exception("Portfolio chat price history failed")
        return None


def period_returns(series: EurSeries) -> dict:
    """1W / 1M / YTD return (%) of the back-tested portfolio and the S&P 500, to the last close."""
    last = series.index[-1]
    value = series.portfolio_value()
    starts = {
        "1W": last - pd.Timedelta(days=7),
        "1M": last - pd.DateOffset(months=1),
        "YTD": pd.Timestamp(year=last.year - 1, month=12, day=31),
    }
    periods = {}
    for label, start in starts.items():
        base_dates = series.index[series.index <= start]
        if base_dates.empty:
            continue
        base = base_dates[-1]
        periods[label] = {
            "portfolio": round(float(value[last] / value[base] - 1) * 100, 2),
            "benchmark": round(float(series.benchmark[last] / series.benchmark[base] - 1) * 100, 2),
        }
    return {"as_of": last.date().isoformat(), "periods": periods}


def _eur(value: float, signed: bool = False) -> str:
    sign = ("+" if value >= 0 else "-") if signed else ("-" if value < 0 else "")
    return f"{sign}€{abs(value):,.0f}"


def _pct(value: float) -> str:
    return f"{value:+.2f}%"


def _holding_line(row: dict, risk: dict | None) -> str:
    label = row["ticker"] + (f" ({row['name']})" if row.get("name") else "")
    if row.get("value") is None:
        return f"- {label}: price unavailable, excluded from totals"
    parts = [
        f"weight {row['weight']:.1f}%",
        f"value {_eur(row['value'])}",
        f"today {_pct(row['day_change_percent'])} ({_eur(row['day_change'], signed=True)})",
        f"return vs avg cost {_eur(row['total_return'], signed=True)} ({_pct(row['total_return_percent'])})",
    ]
    parts += [str(row[k]) for k in ("sector", "country", "asset_type") if row.get(k)]
    metrics = (risk or {}).get("holdings", {}).get(row["ticker"]) or {}
    if metrics.get("beta") is not None:
        parts.append(f"beta {metrics['beta']:.2f}")
    if metrics.get("vol_1y") is not None:
        parts.append(f"1y volatility {metrics['vol_1y']:.1f}%")
    return f"- {label}: " + ", ".join(parts)


def format_context(snapshot: PortfolioSnapshot) -> str:
    summary = snapshot.portfolio["summary"]
    rows = snapshot.portfolio["holdings"]
    if not rows:
        return "Portfolio: the user has no holdings yet."

    unpriced = summary["holdings_count"] - summary["priced_count"]
    lines = [
        "Portfolio summary (EUR):",
        f"- Total value {_eur(summary['total_value'])}; today {_eur(summary['day_change'], signed=True)} "
        f"({_pct(summary['day_change_percent'])})",
        f"- Total return vs cost {_eur(summary['total_return'], signed=True)} ({_pct(summary['total_return_percent'])}) "
        f"on cost {_eur(summary['total_cost'])}",
        f"- {summary['holdings_count']} holdings" + (f", {unpriced} without a price" if unpriced else ""),
        "",
        "Holdings (largest first):",
        *[_holding_line(r, snapshot.risk) for r in rows],
        "",
    ]

    if snapshot.returns and snapshot.returns["periods"]:
        lines.append(
            f"Performance vs S&P 500 (EUR, current shares back-tested to the {snapshot.returns['as_of']} close):"
        )
        for label, r in snapshot.returns["periods"].items():
            lines.append(f"- {label}: portfolio {_pct(r['portfolio'])} vs S&P 500 {_pct(r['benchmark'])}")
    else:
        lines.append("Performance vs S&P 500: unavailable")
    lines.append("")

    if snapshot.risk is None:
        lines.append("Risk metrics: unavailable")
    else:
        p, c = snapshot.risk["portfolio"], snapshot.risk["concentration"]
        lines.append("Risk (last 1 year, EUR):")
        if p["vol_1y"] is not None:
            beta = f"beta {p['beta']:.2f}, " if p["beta"] is not None else ""
            lines.append(f"- Portfolio {beta}volatility {p['vol_1y']:.1f}%, max drawdown {p['max_drawdown_1y']:.1f}%")
        else:
            lines.append("- Portfolio beta/volatility/drawdown: unavailable (not enough price history)")
        if c["top3_weight"] is not None:
            lines.append(
                f"- Top 3 holdings {c['top3_weight']:.1f}% of value; largest sector {c['largest_sector']['name']} "
                f"{c['largest_sector']['weight']:.1f}%; largest country {c['largest_country']['name']} "
                f"{c['largest_country']['weight']:.1f}%"
            )
    return "\n".join(lines).strip()


def _format_conversation(messages: list[dict[str, str]] | None) -> str:
    lines = []
    for msg in (messages or [])[-6:]:
        role = (msg.get("role") or "").upper()
        content = re.sub(r"\s+", " ", msg.get("content") or "").strip()
        if role and content:
            lines.append(f"{role}: {content}")
    return "Recent conversation:\n" + "\n".join(lines) if lines else ""


def _base_symbol(ticker: str) -> str:
    return ticker.split(".")[0]


def mentioned_holdings(question: str, rows: list[dict]) -> list[dict]:
    """Holdings named in the question by ticker (case-sensitive, with or without exchange suffix) or by
    the first word of the company name ("Tesla, Inc." → "tesla", case-insensitive, 4+ letters)."""

    def named(term: str, flags: int = 0) -> bool:
        return re.search(rf"(?<![\w.]){re.escape(term)}(?!\w)", question, flags) is not None

    found = []
    for row in rows:
        tickers = {row["ticker"], _base_symbol(row["ticker"])}
        brand = re.sub(r"\W", "", (row.get("name") or "").split(" ")[0])
        if any(named(t) for t in tickers) or (len(brand) >= 4 and named(brand, re.IGNORECASE)):
            found.append(row)
    return found


def search_targets(question: str, rows: list[dict], scope_ticker: str | None) -> tuple[list[dict], bool]:
    """Holdings to search news for, and whether they came from the question/scope (vs today's movers)."""
    if scope_ticker:
        return [r for r in rows if r["ticker"] == scope_ticker], True
    named = mentioned_holdings(question, rows)
    if named:
        return named[:MAX_SEARCH_HOLDINGS], True
    movers = sorted(
        (r for r in rows if r.get("day_change") is not None), key=lambda r: abs(r["day_change"]), reverse=True
    )
    return movers[:MAX_SEARCH_HOLDINGS], False


def _build_sources_block(sources: list[AnalyzeSource], passages: list[AnalyzePassage]) -> str:
    by_source: dict[str, list[AnalyzePassage]] = {}
    for passage in passages:
        by_source.setdefault(passage.source_id, []).append(passage)
    blocks = []
    for index, source in enumerate(sources, start=1):
        published = source.published_at.isoformat() if source.published_at else "unknown date"
        content = [f"Passage [{p.passage_index}]: {p.content}" for p in by_source.get(source.id, [])]
        if not content and source.raw_content:
            content = [f"Content: {source.raw_content[:1500]}"]
        blocks.append(
            "\n".join(
                [
                    f"Source [{index}]",
                    f"Title: {source.title}",
                    f"Publisher: {source.publisher}",
                    f"Published: {published}",
                    *content,
                ]
            )
        )
    return "\n\n".join(blocks)


def build_answer_prompt(
    *,
    question: str,
    context: str,
    conversation: str,
    scope_ticker: str | None,
    searched: bool,
    external_context: str,
) -> str:
    focus = (
        f"The user is asking about their {scope_ticker} position; the rest of the portfolio is for reference.\n"
        if scope_ticker
        else ""
    )
    search = ""
    if external_context:
        search = f"\nNews search results:\n{external_context}\n"
    elif searched:
        search = "\nNews search found no relevant recent articles. Say so if the question depends on news.\n"
    return f"""
You are a portfolio analyst inside Stonkie. Answer the user's question about their own portfolio using the data below.

Current question:
{question}
{focus}
{context}

{conversation}
{search}
Rules:
- Answer in the same language as the current question.
- Ground every number in the portfolio data or news search results. Never invent prices, dates, events or figures.
- If the data needed is missing or marked unavailable, say so plainly.
- Analysis only: explain facts, drivers, exposures and trade-offs. Never tell the user to buy, sell, trim, add or rebalance, and never suggest target weights or amounts.
- Amounts are in EUR.
- Do not include URLs or a "Sources:" section.
- Keep the answer under 150 words unless the user asks for depth. Start with the direct answer; use short paragraphs or up to 4 bullets.
- End with a separate final line exactly: "{DISCLAIMER}"
    """.strip()


def _extract_answer_text(chunks: list) -> str:
    return "".join(c.get("body", "") for c in chunks if isinstance(c, dict) and c.get("type") == "answer")


def _json_block(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("JSON block is not an object")
    return parsed


class PortfolioChatStreamService:
    def __init__(self, portfolio: PortfolioConnector, yf_client: YFinanceClient) -> None:
        self._portfolio = portfolio
        self._yf_client = yf_client

    @observe(name="portfolio_chat_classify")
    def _classify(
        self, *, question: str, tickers: list[str], conversation: str, preferred_model: ModelName
    ) -> ChatRoute:
        prompt = f"""
You are a strict JSON classifier for a chat about the user's stock portfolio.

Holdings: {", ".join(tickers) or "none"}

Classify the user's current question into exactly one route:
- portfolio_only: answerable from portfolio data alone (weights, values, P/L, today's % moves, performance vs S&P 500, beta, volatility, drawdown, sector/country concentration).
- needs_search: needs current outside information, e.g. why a holding or the portfolio moved, news, earnings, company events, macro or market drivers.
- unrelated: not about the portfolio, markets, finance, companies or investing.

Current question:
{question}

{conversation}

Output ONLY JSON:
{{"route":"portfolio_only|needs_search|unrelated","reason":"short reason"}}
        """.strip()
        try:
            agent = MultiAgent(model_name=preferred_model)
            raw = "".join(
                chunk
                for chunk in agent.generate_content(prompt=prompt, use_google_search=False)
                if isinstance(chunk, str)
            )
            route = _json_block(raw).get("route")
            if route in ("portfolio_only", "needs_search", "unrelated"):
                return route
        except Exception:
            logger.exception("Portfolio chat classification failed; answering from portfolio data")
        return "portfolio_only"

    def _search(
        self, question: str, targets: list[dict], named: bool
    ) -> tuple[list[AnalyzeSource], list[AnalyzePassage]]:
        brave_client = BraveClient(api_key=os.getenv("BRAVE_API_KEY", ""))
        sources: list[AnalyzeSource] = []
        passages: list[AnalyzePassage] = []
        seen: set[str] = set()
        for row in targets:
            symbol = _base_symbol(row["ticker"])
            name = row.get("name") or symbol
            try:
                result = retrieve_for_analyze(
                    question=question if named else f"Why did {name} stock move today?",
                    market=resolve_market(row.get("country"), question),
                    request_id=str(uuid.uuid4()),
                    brave_client=brave_client,
                    ticker=symbol,
                    company_name=name,
                    query_reformulator=QueryReformulator() if named else None,
                )
            except BraveRetrievalError:
                logger.warning("Portfolio chat search found nothing for %s", row["ticker"])
                continue
            for source in result.sources:
                if source.id not in seen:
                    seen.add(source.id)
                    sources.append(source)
            passages += result.selected_passages
        return sources, passages

    @observe(
        name="portfolio_chat.stream",
        as_type="generation",
        capture_input=False,
        transform_to_string=_extract_answer_text,
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
        history = get_conversation_history_for_prompt(user_id, CONVERSATION_SCOPE, conv_id)
        append_user_message(user_id, CONVERSATION_SCOPE, conv_id, question)
        yield {"type": "conversation", "body": {"conversationId": conv_id}}

        yield thinking_status("Reading your portfolio…", phase=AnalysisPhase.ANALYZE, step=1, total_steps=3)
        try:
            snapshot = await load_snapshot(user_id, self._portfolio, self._yf_client)
        except PortfolioUnavailableError:
            logger.exception("Portfolio chat could not load the portfolio")
            yield {"type": "error", "code": "portfolio_unavailable", "body": "Couldn't load your portfolio"}
            return
        rows = snapshot.portfolio["holdings"]
        conversation = _format_conversation(history)

        route = self._classify(
            question=question,
            tickers=[r["ticker"] for r in rows],
            conversation=conversation,
            preferred_model=preferred_model,
        )
        if langfuse:
            langfuse.update_current_generation(metadata={"scope_ticker": scope_ticker, "route": route})
        if route == "unrelated":
            yield {"type": "answer", "body": UNRELATED_ANSWER}
            append_assistant_message(user_id, CONVERSATION_SCOPE, conv_id, UNRELATED_ANSWER)
            return

        sources: list[AnalyzeSource] = []
        external_context = ""
        searched = False
        if route == "needs_search":
            targets, named = search_targets(question, rows, scope_ticker)
            if targets:
                searched = True
                tickers = ", ".join(_base_symbol(r["ticker"]) for r in targets)
                yield thinking_status(
                    f"Searching news for {tickers}…", phase=AnalysisPhase.SEARCH, step=2, total_steps=3
                )
                sources, passages = await asyncio.to_thread(self._search, question, targets, named)
                external_context = _build_sources_block(sources, passages)
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
        output: list[str] = []
        splitter = VisualAnswerStreamSplitter()
        agent = MultiAgent(model_name=preferred_model)
        for chunk in agent.generate_content(prompt=prompt, use_google_search=False):
            if await is_disconnected():
                return
            if not isinstance(chunk, str):
                continue
            for event in splitter.process_text(chunk):
                if event.get("type") == "answer" and isinstance(event.get("body"), str):
                    output.append(event["body"])
                    if not ttft_recorded and langfuse:
                        langfuse.update_current_generation(completion_start_time=datetime.datetime.now())
                        ttft_recorded = True
                yield event
        for event in splitter.finalize():
            if event.get("type") == "answer" and isinstance(event.get("body"), str):
                output.append(event["body"])
            yield event

        if sources:
            yield build_sources_event(sources)
        yield {"type": "model_used", "body": agent.model_name}
        if output:
            append_assistant_message(user_id, CONVERSATION_SCOPE, conv_id, "".join(output))
