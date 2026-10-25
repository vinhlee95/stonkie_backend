"""ETFComparisonHandlerV2: ETF-vs-ETF, per-ticker Brave fan-out (Semaphore 5), partial-failure semantics.

Fakes only: no LLM, DB or Brave calls.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from ai_models.model_name import ModelName
from services.analyze_retrieval.schemas import BraveRetrievalError
from services.etf_question_analyzer.comparison_handler_v2 import ETFComparisonHandlerV2
from services.etf_question_analyzer.types import ETFDataRequirement, ETFQuestionType
from services.question_analyzer.context_builders.components import PromptComponents
from services.question_analyzer.router import MAX_QUERY_CHARS, MAX_QUERY_WORDS, ETFRoutingResult
from services.search_decision_engine import SearchDecision
from tests.services.etf_question_analyzer.etf_fakes import (
    FakeAgentFactory,
    FakeRetriever,
    make_etf,
    make_retrieval,
    make_source,
    two_related_questions,
)

ETFS = {
    "SXR8": make_etf("SXR8"),
    "VUSA": make_etf("VUSA", name="Vanguard S&P 500 UCITS ETF", fund_provider="Vanguard"),
    "CSPX": make_etf("CSPX", name="iShares Core S&P 500 UCITS ETF USD (Acc)"),
}


class FakeETFConnector:
    def __init__(self, etfs: dict | None = None, errors: set[str] | None = None):
        self.etfs = ETFS if etfs is None else etfs
        self.errors = errors or set()
        self.calls: list[str] = []

    def get_by_ticker(self, ticker: str):
        self.calls.append(ticker)
        if ticker in self.errors:
            raise RuntimeError("db down")
        return self.etfs.get(ticker)


def _routing(*, search: bool = True, standalone: str = "Compare SXR8 vs VUSA fees") -> ETFRoutingResult:
    return ETFRoutingResult(
        question_type=ETFQuestionType.ETF_OVERVIEW,
        data_requirement=ETFDataRequirement.BASIC,
        search_decision=SearchDecision(
            use_google_search=search,
            reason_code="latest_info" if search else "db_data_sufficient",
            confidence=0.9,
            decision_model="router:test",
            decision_fallback="none",
        ),
        search_queries=["q"] if search else [],
        standalone_question=standalone,
    )


def _per_company_retriever(**overrides: Any) -> FakeRetriever:
    by_company = {
        etf.name: make_retrieval(make_source(f"{t}_1", url=f"https://www.justetf.com/{t}")) for t, etf in ETFS.items()
    }
    by_company.update(overrides)
    return FakeRetriever(by_company=by_company)


def _handler(connector=None, retriever=None):
    factory = FakeAgentFactory()
    handler = ETFComparisonHandlerV2(
        connector=connector or FakeETFConnector(),
        retriever=retriever or _per_company_retriever(),
        brave_client=object(),
        agent_factory=factory,
        related_questions=two_related_questions,
    )
    return handler, factory.agent


async def _run(handler, **kwargs) -> list[dict]:
    params: dict[str, Any] = {
        "tickers": ["SXR8", "VUSA"],
        "question": "Compare SXR8 vs VUSA fees",
        "routing": _routing(),
        "short_analysis": True,
        "preferred_model": ModelName.Auto,
        "request_id": "req-cmp",
    }
    params.update(kwargs)
    return [e async for e in handler.handle(**params)]


def _types(events):
    return [e["type"] for e in events]


def _statuses(events):
    return [e["body"] for e in events if e["type"] == "thinking_status"]


@pytest.mark.asyncio
async def test_search_on_fans_out_per_ticker_with_etf_policy_and_grounding():
    retriever = _per_company_retriever()
    handler, agent = _handler(retriever=retriever)
    events = await _run(handler)

    assert sorted(c["ticker"] for c in retriever.calls) == ["SXR8", "VUSA"]
    for call in retriever.calls:
        assert call["asset_class"] == "etf"
        assert call["market"] == "GLOBAL"
        assert call["question"] == "Compare SXR8 vs VUSA fees"
        assert call["company_name"] == ETFS[call["ticker"]].name
        (query,) = call["query_reformulator"].reformulate("x", ticker="", company_name="").queries
        assert query.startswith(ETFS[call["ticker"]].name)

    prompt = agent.prompts[0]
    assert PromptComponents.grounding_rules() in prompt
    assert "CRITICAL - Source Citation" not in prompt
    assert "Passage from SXR8_1" in prompt and "Passage from VUSA_1" in prompt
    assert agent.search_flags == [False]

    sources = next(e for e in events if e["type"] == "sources")["body"]
    assert [s["source_id"] for s in sources] == ["SXR8_1", "VUSA_1"]  # ticker order
    assert any("across SXR8, VUSA" in s for s in _statuses(events))
    types = _types(events)
    last_answer = max(i for i, t in enumerate(types) if t == "answer")
    assert types[last_answer + 1 :] == ["sources", "model_used", "related_question", "related_question"]


@pytest.mark.asyncio
async def test_search_off_skips_brave_and_sources():
    retriever = _per_company_retriever()
    handler, agent = _handler(retriever=retriever)
    events = await _run(handler, routing=_routing(search=False))

    assert retriever.calls == []
    assert "sources" not in _types(events)
    assert "iShares Core S&P 500 UCITS ETF" in agent.prompts[0]
    assert "Vanguard S&P 500 UCITS ETF" in agent.prompts[0]


@pytest.mark.asyncio
async def test_partial_failure_proceeds_with_the_rest_and_notes_it_in_prompt():
    retriever = _per_company_retriever(**{ETFS["VUSA"].name: BraveRetrievalError("down")})
    handler, agent = _handler(retriever=retriever)
    events = await _run(handler)

    assert any("Web retrieval failed for VUSA" in s for s in _statuses(events))
    assert "web retrieval failed for VUSA" in agent.prompts[0]
    sources = next(e for e in events if e["type"] == "sources")["body"]
    assert [s["source_id"] for s in sources] == ["SXR8_1"]


@pytest.mark.asyncio
async def test_all_retrievals_failing_raises():
    retriever = FakeRetriever(result=BraveRetrievalError("down"))
    handler, agent = _handler(retriever=retriever)
    with pytest.raises(BraveRetrievalError):
        await _run(handler)
    assert agent.prompts == []


@pytest.mark.asyncio
async def test_missing_etf_is_dropped_with_a_status_and_rest_compared():
    connector = FakeETFConnector(errors={"CSPX"})
    handler, agent = _handler(connector=connector)
    events = await _run(handler, tickers=["SXR8", "VUSA", "CSPX", "NOPE"], routing=_routing(search=False))

    assert any("Couldn't find" in s and "CSPX" in s and "NOPE" in s for s in _statuses(events))
    assert "Comparing SXR8, VUSA..." in _statuses(events)
    assert "CSPX" not in agent.prompts[0].split("## User Question")[0]


@pytest.mark.asyncio
async def test_fewer_than_two_etfs_is_an_error():
    handler, agent = _handler(connector=FakeETFConnector(etfs={"SXR8": ETFS["SXR8"]}))
    events = await _run(handler)
    assert events[-1]["type"] == "error"
    assert "VUSA" in events[-1]["body"]
    assert agent.prompts == []


@pytest.mark.asyncio
async def test_fan_out_is_capped_at_five_concurrent_retrievals():
    tickers = [f"T{i}" for i in range(8)]
    etfs = {t: make_etf(t, name=f"Fund {t}") for t in tickers}
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    class _Slow(FakeRetriever):
        def __call__(self, **kwargs):
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            time.sleep(0.05)
            with lock:
                state["active"] -= 1
            return super().__call__(**kwargs)

    retriever = _Slow()
    handler, _ = _handler(connector=FakeETFConnector(etfs=etfs), retriever=retriever)
    await _run(handler, tickers=tickers)

    assert len(retriever.calls) == 8
    assert 1 < state["peak"] <= 5


@pytest.mark.asyncio
async def test_follow_up_uses_standalone_question_in_prompt_and_retrieval():
    retriever = _per_company_retriever()
    handler, agent = _handler(retriever=retriever)
    routing = _routing(standalone="How do SXR8 and VUSA fees compare?")
    history = [{"role": "user", "content": "What are SXR8 fees?"}, {"role": "assistant", "content": "0.07%"}]
    await _run(handler, question="And versus VUSA?", routing=routing, conversation_messages=history)

    assert {c["question"] for c in retriever.calls} == {"How do SXR8 and VUSA fees compare?"}
    assert '"How do SXR8 and VUSA fees compare?"' in agent.prompts[0]


@pytest.mark.asyncio
async def test_comparison_queries_are_short_fund_name_ticker_plus_trimmed_question():
    long_question = "Compare SXR8 vs VUSA on " + " ".join(f"aspect{i}" for i in range(150))
    retriever = _per_company_retriever()
    handler, _ = _handler(retriever=retriever)
    await _run(handler, question=long_question, routing=_routing(standalone=long_question))

    assert len(retriever.calls) == 2
    for call in retriever.calls:
        (query,) = call["query_reformulator"].reformulate("x", ticker="", company_name="").queries
        assert len(query) <= MAX_QUERY_CHARS
        assert len(query.split()) <= MAX_QUERY_WORDS
        assert query.startswith(f"{ETFS[call['ticker']].name} {call['ticker']} Compare SXR8 vs VUSA")


@pytest.mark.asyncio
async def test_duplicate_tickers_are_compared_and_retrieved_once():
    connector = FakeETFConnector()
    retriever = _per_company_retriever()
    handler, agent = _handler(connector=connector, retriever=retriever)
    events = await _run(handler, tickers=["SXR8", "VUSA", "sxr8"])

    assert connector.calls == ["SXR8", "VUSA"]
    assert sorted(c["ticker"] for c in retriever.calls) == ["SXR8", "VUSA"]
    assert "Comparing SXR8, VUSA..." in _statuses(events)
    assert agent.prompts[0].count("### ") == 2


@pytest.mark.asyncio
async def test_a_fund_compared_with_itself_is_an_error():
    retriever = _per_company_retriever()
    handler, agent = _handler(retriever=retriever)
    events = await _run(handler, tickers=["SXR8", "SXR8"])
    assert events[-1]["type"] == "error"
    assert retriever.calls == []
    assert agent.prompts == []
