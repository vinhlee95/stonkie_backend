"""SPIKE / throwaway: end-to-end validation of the per-ticker news recap idea.

This is NOT the production pipeline. It wires existing building blocks inline to
prove the whole flow works for a single ticker (default AAPL) against the REAL
yfinance / Brave / LLM stack, then persists to the `ticker_recap` table.

The two novel-vs-market_recap parts (move-aware query gen + ticker recap gen) are
implemented inline here so we can eyeball quality before building services/ticker_recap/*.

Usage:
    source venv/bin/activate
    PYTHONPATH=. python scripts/spike_ticker_recap.py
    PYTHONPATH=. python scripts/spike_ticker_recap.py --ticker NVDA --no-persist
    PYTHONPATH=. python scripts/spike_ticker_recap.py --period-start 2026-06-18 --period-end 2026-06-18

Requires BRAVE_API_KEY + LLM keys in .env.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date

from dotenv import load_dotenv

from agent.multi_agent import MultiAgent
from connectors.database import SessionLocal
from connectors.yfinance_client import YFinanceClient
from models.ticker_recap import TickerRecap
from scripts.run_market_recap import compute_latest_completed_trading_day
from services.market_recap.retrieval import retrieve_candidates
from services.market_recap.schemas import PlannedQuery, RetrievalResult
from services.price_change import get_price_changes

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("spike_ticker_recap")

# Spike scope: just AAPL by default. The {name, market} map mirrors the planned
# POPULAR_TICKERS config so the recap/query prompts read naturally.
TICKERS = {
    "AAPL": {"name": "Apple Inc.", "market": "US"},
    "NVDA": {"name": "NVIDIA Corporation", "market": "US"},
    "TSLA": {"name": "Tesla, Inc.", "market": "US"},
    "GOOG": {"name": "Alphabet Inc.", "market": "US"},
}

BIG_MOVE_THRESHOLD_PCT = 3.0


# --- Stage 1: move-aware query generation (inline LLM) -----------------------


def build_query_prompt(ticker: str, company_name: str, price_change: dict | None) -> str:
    lines = [
        f"You craft ONE web search query to find news explaining today's stock move for {company_name} ({ticker}).",
        "Return ONLY the raw search query text. No quotes, no preamble, no explanation.",
    ]
    if price_change is None:
        lines.append(f"No price data available; produce a neutral query for the latest {ticker} news.")
        return "\n".join(lines)

    pct = price_change.get("change_percent")
    lines.append(
        f"Trading date: {price_change.get('trading_date')}. "
        f"Close {price_change.get('close')} vs prev {price_change.get('prev_close')} "
        f"({pct:+.2f}%)."
    )
    if pct is not None and abs(pct) >= BIG_MOVE_THRESHOLD_PCT:
        direction = "jumped" if pct > 0 else "fell"
        lines.append(
            f"This is a BIG move. Frame the query causally, e.g. why {ticker} {direction} "
            f"{abs(pct):.1f}% today / what drove the move."
        )
    else:
        lines.append(f"This is a small/flat move. A neutral 'latest {ticker} stock news today' framing is fine.")
    return "\n".join(lines)


def generate_query(ticker: str, company_name: str, price_change: dict | None, agent: MultiAgent) -> str:
    prompt = build_query_prompt(ticker, company_name, price_change)
    chunks = agent.generate_content(prompt=prompt, use_google_search=False)
    raw = "".join(chunk for chunk in chunks if isinstance(chunk, str)).strip()
    # LLM occasionally wraps in quotes / adds a trailing newline; normalize.
    query = raw.strip().strip('"').strip()
    if not query:
        raise ValueError("LLM returned an empty query")
    return query.splitlines()[0].strip()


# --- Stage 2: retrieval (reuse market_recap pipeline with our query) ---------


def retrieve(query: str, market: str, period_start: date, period_end: date, top_k: int = 5) -> RetrievalResult:
    return retrieve_candidates(
        market=market,
        period_start=period_start,
        period_end=period_end,
        planned_queries=[PlannedQuery(query=query)],
        top_k=top_k,
    )


# --- Stage 3: ticker recap generation (inline LLM, structured) ---------------


def build_recap_prompt(
    ticker: str,
    company_name: str,
    price_change: dict | None,
    retrieval: RetrievalResult,
    trading_date: date,
) -> str:
    move_line = "Price move: unavailable."
    if price_change is not None:
        move_line = (
            f"Price move on {price_change.get('trading_date')}: close {price_change.get('close')} "
            f"vs prev {price_change.get('prev_close')} ({price_change.get('change_percent'):+.2f}%)."
        )
    lines = [
        f"Generate a daily news recap JSON for the single stock {company_name} ({ticker}) for {trading_date.isoformat()}.",
        move_line,
        "Use ONLY this schema:",
        '{"summary":"string","bullets":[{"text":"string","source_indices":[0]}]}',
        "Rules:",
        "- summary must be non-empty plain text about THIS stock and its move today.",
        "- bullets must contain 3-6 items, each explaining a driver of the move or key news.",
        "- each bullet must include at least one integer index in source_indices.",
        "- source_indices must reference only provided Source [i] blocks.",
        f"- ground every claim in the sources; do not invent numbers or events for {ticker}.",
        "- focus on THIS ticker; ignore broad-market filler unless it directly explains the move.",
        "Return JSON wrapped in [RECAP_JSON]...[/RECAP_JSON].",
    ]
    for idx, candidate in enumerate(retrieval.candidates):
        lines.extend(
            [
                f"Source [{idx}]",
                f"Title: {candidate.title}",
                f"URL: {candidate.url}",
                f"Published: {candidate.published_date.isoformat() if candidate.published_date else 'null'}",
                f"Content: {candidate.raw_content}",
            ]
        )
    return "\n".join(lines)


def _extract_json_block(raw_text: str) -> str:
    start_tag, end_tag = "[RECAP_JSON]", "[/RECAP_JSON]"
    start, end = raw_text.find(start_tag), raw_text.find(end_tag)
    if start < 0 or end < 0 or end <= start:
        raise ValueError("missing recap json markers")
    return raw_text[start + len(start_tag) : end]


def generate_ticker_recap(
    ticker: str,
    company_name: str,
    price_change: dict | None,
    retrieval: RetrievalResult,
    trading_date: date,
    agent: MultiAgent,
) -> tuple[dict, list[dict], list[dict], str]:
    """Returns (summary_obj, bullets, sources, model). summary_obj == {"summary": str}."""
    prompt = build_recap_prompt(ticker, company_name, price_change, retrieval, trading_date)
    chunks = agent.generate_content(prompt=prompt, use_google_search=False)
    raw_text = "".join(chunk for chunk in chunks if isinstance(chunk, str))
    payload = json.loads(_extract_json_block(raw_text))

    summary = payload.get("summary", "")
    raw_bullets = payload.get("bullets", [])
    sources_by_url: dict[str, dict] = {}
    bullets: list[dict] = []
    for bullet in raw_bullets:
        citation_urls = []
        for source_index in bullet.get("source_indices", []):
            if source_index < 0 or source_index >= len(retrieval.candidates):
                raise ValueError(f"source index out of range: {source_index}")
            candidate = retrieval.candidates[source_index]
            sources_by_url[candidate.canonical_url] = {
                "url": candidate.canonical_url,
                "title": candidate.title,
                "published_at": candidate.published_date.isoformat() if candidate.published_date else None,
            }
            citation_urls.append(candidate.canonical_url)
        bullets.append({"text": bullet.get("text", ""), "citations": citation_urls})

    return {"summary": summary}, bullets, list(sources_by_url.values()), getattr(agent, "model_name", "") or ""


# --- Stage 4: persist --------------------------------------------------------


def persist(
    *,
    ticker: str,
    cadence: str,
    period_start: date,
    period_end: date,
    summary: str,
    bullets: list[dict],
    sources: list[dict],
    price_change: dict | None,
    search_query: str,
    model: str,
) -> int:
    with SessionLocal() as db:
        existing = (
            db.query(TickerRecap).filter_by(ticker=ticker, cadence=cadence, period_start=period_start).one_or_none()
        )
        if existing is not None:
            db.delete(existing)
            db.flush()
        row = TickerRecap(
            ticker=ticker,
            cadence=cadence,
            period_start=period_start,
            period_end=period_end,
            summary=summary,
            bullets=bullets,
            sources=sources,
            raw_sources=None,
            price_change=price_change,
            search_query=search_query,
            model=model,
        )
        db.add(row)
        db.commit()
        return row.id


# --- Orchestration -----------------------------------------------------------


def run_for_ticker(
    ticker: str,
    *,
    cadence: str,
    period_start: date,
    period_end: date,
    persist_row: bool,
) -> bool:
    meta = TICKERS.get(ticker, {"name": ticker, "market": "US"})
    company_name, market = meta["name"], meta["market"]
    agent = MultiAgent()

    print(f"\n{'=' * 70}\n{ticker} ({company_name}) — {period_start.isoformat()} [{market}]\n{'=' * 70}")

    price_changes = get_price_changes([ticker], YFinanceClient())
    price_change = price_changes.get(ticker)
    if price_change is None:
        print("  ! No price change available — would SKIP in production.")
    else:
        print(
            f"  price: {price_change['change_percent']:+.2f}%  ({price_change['prev_close']} -> {price_change['close']})"
        )

    query = generate_query(ticker, company_name, price_change, agent)
    print(f"  query: {query!r}")

    retrieval = retrieve(query, market, period_start, period_end)
    print(f"  retrieval: {retrieval.stats.results_total} raw -> {len(retrieval.candidates)} in-window top_k")
    for c in retrieval.candidates:
        pub = c.published_date.date().isoformat() if c.published_date else "?"
        print(f"    - [{pub}] {c.title[:70]}")
    if not retrieval.candidates:
        print("  ! Zero in-window candidates — would SKIP persist in production.")
        return False

    summary_obj, bullets, sources, model = generate_ticker_recap(
        ticker, company_name, price_change, retrieval, period_start, agent
    )
    summary = summary_obj["summary"]
    if not summary or len(bullets) < 3:
        print(f"  ! Recap looks empty/thin (summary={bool(summary)}, bullets={len(bullets)}) — would SKIP.")
        return False

    print(f"\n  SUMMARY: {summary}\n")
    for b in bullets:
        print(f"   • {b['text']}")
        for url in b["citations"]:
            print(f"       ↳ {url}")

    if persist_row:
        recap_id = persist(
            ticker=ticker,
            cadence=cadence,
            period_start=period_start,
            period_end=period_end,
            summary=summary,
            bullets=bullets,
            sources=sources,
            price_change=price_change,
            search_query=query,
            model=model,
        )
        print(f"\n  ✓ persisted ticker_recap id={recap_id}")
    else:
        print("\n  (--no-persist: not written)")
    return True


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="SPIKE: end-to-end per-ticker recap validation")
    parser.add_argument("--ticker", default="AAPL", help="Ticker to run (default AAPL)")
    parser.add_argument("--cadence", default="daily")
    parser.add_argument("--period-start")
    parser.add_argument("--period-end")
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args(argv)

    ticker = args.ticker.upper()
    if args.period_start and args.period_end:
        period_start = date.fromisoformat(args.period_start)
        period_end = date.fromisoformat(args.period_end)
    else:
        trading_day = compute_latest_completed_trading_day("US")
        period_start = period_end = trading_day

    ok = run_for_ticker(
        ticker,
        cadence=args.cadence,
        period_start=period_start,
        period_end=period_end,
        persist_row=not args.no_persist,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
