from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from services import portfolio_chat
from services.analyze_retrieval.schemas import AnalyzeRetrievalResult, AnalyzeSource, BraveRetrievalError
from services.portfolio_chat import (
    UNRELATED_ANSWER,
    PortfolioChatStreamService,
    PortfolioSnapshot,
    PortfolioUnavailableError,
    format_context,
    load_snapshot,
    mentioned_holdings,
    period_returns,
    search_targets,
)
from services.portfolio_performance import EurSeries


def row(ticker, value, weight, day_change, day_pct, **extra):
    return {
        "ticker": ticker,
        "name": extra.pop("name", None),
        "value": value,
        "weight": weight,
        "day_change": day_change,
        "day_change_percent": day_pct,
        "total_return": extra.pop("total_return", 100.0),
        "total_return_percent": extra.pop("total_return_percent", 10.0),
        "sector": extra.pop("sector", "Technology"),
        "country": extra.pop("country", "United States"),
        "asset_type": extra.pop("asset_type", "Stock"),
        **extra,
    }


ROWS = [
    row("AAPL", 6000.0, 60.0, 120.0, 2.04, name="Apple Inc"),
    row("TSLA", 3000.0, 30.0, -240.0, -7.41, name="Tesla, Inc.", sector="Consumer Cyclical"),
    row("NOKIA.HE", 1000.0, 10.0, 11.0, 1.11, name="Nokia Oyj", country="Finland"),
    row("KNEBV.HE", None, None, None, None, name="KONE Oyj", country="Finland"),
]
SUMMARY = {
    "holdings_count": 4,
    "priced_count": 3,
    "total_value": 10000.0,
    "total_cost": 8000.0,
    "total_return": 2000.0,
    "total_return_percent": 25.0,
    "day_change": -109.0,
    "day_change_percent": -1.08,
}
RISK = {
    "holdings": {"AAPL": {"beta": 1.18, "vol_1y": 24.3}, "TSLA": {"beta": None, "vol_1y": None}},
    "portfolio": {"beta": 1.34, "vol_1y": 24.6, "max_drawdown_1y": -18.2},
    "concentration": {
        "top3_weight": 100.0,
        "largest_sector": {"name": "Technology", "weight": 70.0},
        "largest_country": {"name": "United States", "weight": 90.0},
    },
}
RETURNS = {"as_of": "2026-10-02", "periods": {"1W": {"portfolio": 1.2, "benchmark": 0.4}}}


def snapshot(returns=RETURNS, risk=RISK, rows=ROWS):
    return PortfolioSnapshot(portfolio={"summary": SUMMARY, "holdings": rows}, returns=returns, risk=risk)


def test_format_context_lists_holdings_performance_and_risk():
    text = format_context(snapshot())

    assert "Total value €10,000; today -€109 (-1.08%)" in text
    assert "4 holdings, 1 without a price" in text
    assert "- AAPL (Apple Inc): weight 60.0%, value €6,000, today +2.04% (+€120)" in text
    assert "beta 1.18, 1y volatility 24.3%" in text
    assert "TSLA (Tesla, Inc.)" in text and "beta" not in text.split("TSLA (Tesla, Inc.)")[1].split("\n")[0]
    assert "- KNEBV.HE (KONE Oyj): price unavailable, excluded from totals" in text
    assert "- 1W: portfolio +1.20% vs S&P 500 +0.40%" in text
    assert "Portfolio beta 1.34, volatility 24.6%, max drawdown -18.2%" in text
    assert "largest sector Technology 70.0%" in text


def test_format_context_marks_missing_blocks_unavailable():
    text = format_context(snapshot(returns=None, risk=None))

    assert "Performance vs S&P 500: unavailable" in text
    assert "Risk metrics: unavailable" in text


def test_format_context_empty_portfolio():
    assert format_context(snapshot(rows=[])) == "Portfolio: the user has no holdings yet."


def test_period_returns_from_series():
    index = pd.bdate_range("2025-12-29", "2026-02-27")
    value = pd.Series(range(100, 100 + len(index)), index=index, dtype=float)
    series = EurSeries(
        index=index,
        prices={"A": value},
        shares={"A": 1.0},
        benchmark=pd.Series(100.0, index=index),
        first_close={"A": index[0]},
    )

    returns = period_returns(series)

    last = value.iloc[-1]
    assert returns["as_of"] == "2026-02-27"
    assert returns["periods"]["1W"]["portfolio"] == pytest.approx(round((last / value["2026-02-20"] - 1) * 100, 2))
    assert returns["periods"]["YTD"]["portfolio"] == pytest.approx(round((last / value["2025-12-31"] - 1) * 100, 2))
    assert returns["periods"]["1M"]["benchmark"] == 0.0


def test_mentioned_holdings_by_ticker_suffixless_ticker_and_brand():
    assert [r["ticker"] for r in mentioned_holdings("Why is NOKIA up?", ROWS)] == ["NOKIA.HE"]
    assert [r["ticker"] for r in mentioned_holdings("what about tesla and AAPL", ROWS)] == ["AAPL", "TSLA"]
    assert mentioned_holdings("is my portfolio a good mix", [row("A", 1.0, 1.0, 0.0, 0.0)]) == []


def test_search_targets_scope_then_named_then_biggest_movers():
    assert search_targets("anything", ROWS, "TSLA") == ([ROWS[1]], True)
    assert search_targets("why is nokia up", ROWS, None) == ([ROWS[2]], True)
    targets, named = search_targets("Explain today's move", ROWS, None)
    assert [r["ticker"] for r in targets] == ["TSLA", "AAPL"]
    assert named is False


@pytest.mark.asyncio
async def test_load_snapshot_keeps_concentration_when_price_history_fails():
    portfolio = SimpleNamespace(list_holdings=lambda user_id: [])
    with (
        patch.object(portfolio_chat, "get_portfolio", return_value={"summary": SUMMARY, "holdings": ROWS}),
        patch.object(portfolio_chat, "load_eur_series", side_effect=RuntimeError("yahoo down")),
    ):
        snap = await load_snapshot("user-1", portfolio, None)

    assert snap.returns is None
    assert snap.risk["portfolio"]["vol_1y"] is None
    assert snap.risk["concentration"]["top3_weight"] == 100.0


@pytest.mark.asyncio
async def test_load_snapshot_raises_when_portfolio_fails():
    portfolio = SimpleNamespace(list_holdings=lambda user_id: [])
    with (
        patch.object(portfolio_chat, "get_portfolio", side_effect=RuntimeError("db down")),
        patch.object(portfolio_chat, "load_eur_series", return_value=(None, [])),
        pytest.raises(PortfolioUnavailableError),
    ):
        await load_snapshot("user-1", portfolio, None)


class FakeAgent:
    prompts: list[str] = []
    route = "portfolio_only"
    answer = "TSLA fell on delivery news.\n\nNot financial advice."

    def __init__(self, model_name):
        self.model_name = model_name

    def generate_content(self, *, prompt: str, use_google_search: bool):
        FakeAgent.prompts.append(prompt)
        if "strict JSON classifier" in prompt:
            yield self.route
            return
        yield self.answer


async def _connected():
    return False


async def collect(question="How am I doing?", scope_ticker=None, route='{"route":"portfolio_only"}', snap=None):
    FakeAgent.prompts = []
    FakeAgent.route = route

    async def fake_load(*args):
        if snap is None:
            return snapshot()
        if isinstance(snap, Exception):
            raise snap
        return snap

    with (
        patch.object(portfolio_chat, "MultiAgent", FakeAgent),
        patch.object(portfolio_chat, "load_snapshot", fake_load),
        patch.object(portfolio_chat, "get_conversation_history_for_prompt", return_value=[]),
        patch.object(portfolio_chat, "append_user_message") as append_user,
        patch.object(portfolio_chat, "append_assistant_message") as append_assistant,
    ):
        events = [
            e
            async for e in PortfolioChatStreamService(None, None).stream(
                user_id="user-1",
                question=question,
                scope_ticker=scope_ticker,
                preferred_model="fastest",
                conversation_id="conv-1",
                is_disconnected=_connected,
            )
        ]
    return events, append_user, append_assistant


@pytest.mark.asyncio
async def test_portfolio_only_answer_grounded_in_context():
    events, append_user, append_assistant = await collect()

    types = [e["type"] for e in events]
    assert types[0] == "conversation"
    assert events[0]["body"] == {"conversationId": "conv-1"}
    assert "answer" in types and types[-1] == "model_used"
    assert "sources" not in types
    statuses = [e["body"] for e in events if e["type"] == "thinking_status"]
    assert statuses[0] == "Reading your portfolio…"
    assert not any(s.startswith("Searching") for s in statuses)
    prompt = FakeAgent.prompts[-1]
    assert "Total value €10,000" in prompt
    assert "Never tell the user to buy, sell, trim, add or rebalance" in prompt
    assert 'End with a separate final line exactly: "Not financial advice."' in prompt
    assert "position; the rest of the portfolio is for reference" not in prompt
    append_user.assert_called_once_with("user-1", "portfolio", "conv-1", "How am I doing?")
    append_assistant.assert_called_once()


@pytest.mark.asyncio
async def test_scope_adds_focus_line():
    await collect(question="How much does it add to my risk?", scope_ticker="TSLA")

    assert "asking about their TSLA position" in FakeAgent.prompts[-1]


@pytest.mark.asyncio
async def test_classifier_garbage_falls_back_to_portfolio_only():
    events, _, _ = await collect(route="not json")

    assert "sources" not in [e["type"] for e in events]
    assert "answer" in [e["type"] for e in events]


@pytest.mark.asyncio
async def test_unrelated_question_gets_redirect_without_answer_call():
    events, _, append_assistant = await collect(question="Best pasta recipe?", route='{"route":"unrelated"}')

    assert [e["body"] for e in events if e["type"] == "answer"] == [UNRELATED_ANSWER]
    assert len(FakeAgent.prompts) == 1
    append_assistant.assert_called_once_with("user-1", "portfolio", "conv-1", UNRELATED_ANSWER)


def _source(source_id):
    return AnalyzeSource(
        id=source_id,
        url=f"https://www.reuters.com/{source_id}",
        title="Tesla slides after deliveries",
        publisher="reuters.com",
        published_at=datetime.fromisoformat("2026-10-02T12:00:00+00:00"),
        is_trusted=True,
        raw_content="Tesla fell 7% as investors sold the news.",
    )


@pytest.mark.asyncio
async def test_needs_search_searches_scoped_holding_and_emits_sources():
    calls = []

    def fake_retrieve(**kwargs):
        calls.append(kwargs)
        return AnalyzeRetrievalResult(
            sources=[_source("s1")],
            selected_passages=[],
            query=kwargs["question"],
            market=kwargs["market"],
            request_id=kwargs["request_id"],
        )

    with patch.object(portfolio_chat, "retrieve_for_analyze", side_effect=fake_retrieve):
        events, _, _ = await collect(
            question="Why is it down today?", scope_ticker="TSLA", route='{"route":"needs_search"}'
        )

    assert [(c["ticker"], c["company_name"]) for c in calls] == [("TSLA", "Tesla, Inc.")]
    assert calls[0]["query_reformulator"] is not None
    assert "Searching news for TSLA…" in [e["body"] for e in events if e["type"] == "thinking_status"]
    sources = next(e["body"] for e in events if e["type"] == "sources")
    assert [s["source_id"] for s in sources] == ["s1"]
    assert "Tesla fell 7% as investors sold the news." in FakeAgent.prompts[-1]


@pytest.mark.asyncio
async def test_unscoped_move_question_searches_biggest_movers_and_survives_empty_search():
    calls = []

    def fake_retrieve(**kwargs):
        calls.append(kwargs)
        raise BraveRetrievalError("nothing")

    with patch.object(portfolio_chat, "retrieve_for_analyze", side_effect=fake_retrieve):
        events, _, _ = await collect(question="Explain today's move", route='{"route":"needs_search"}')

    assert [c["ticker"] for c in calls] == ["TSLA", "AAPL"]
    assert calls[0]["question"] == "Why did Tesla, Inc. stock move today?"
    assert calls[0]["query_reformulator"] is None
    assert "sources" not in [e["type"] for e in events]
    assert "News search found no relevant recent articles" in FakeAgent.prompts[-1]


@pytest.mark.asyncio
async def test_portfolio_unavailable_emits_error():
    events, _, append_assistant = await collect(snap=PortfolioUnavailableError("user-1"))

    assert events[-1] == {"type": "error", "code": "portfolio_unavailable", "body": "Couldn't load your portfolio"}
    append_assistant.assert_not_called()
