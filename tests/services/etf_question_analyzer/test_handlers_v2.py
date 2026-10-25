"""ETF v2 handlers: router-driven Brave retrieval (asset_class="etf"), grounding, force-search, event order.

Fakes only (retriever, agent, related questions): no LLM, DB or Brave calls.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ai_models.model_name import ModelName
from services.analyze_retrieval.schemas import BraveRetrievalError
from services.etf_question_analyzer.handlers_v2 import (
    ETFDetailedAnalysisHandlerV2,
    ETFOverviewHandlerV2,
    GeneralETFHandlerV2,
)
from services.etf_question_analyzer.types import ETFDataRequirement, ETFQuestionType
from services.question_analyzer.context_builders.components import PromptComponents
from services.question_analyzer.router import MAX_QUERY_CHARS, MAX_QUERY_WORDS, ETFRoutingResult, trim_search_query
from services.search_decision_engine import SearchDecision
from tests.services.etf_question_analyzer.etf_fakes import (
    FakeAgent,
    FakeAgentFactory,
    FakeRetriever,
    make_etf,
    two_related_questions,
)

GROUNDING_RULES = PromptComponents.grounding_rules()
NO_DATA_DECLINE = PromptComponents.no_data_decline()
V1_SOURCE_MARKER = "CRITICAL - Source Citation"


def _routing(
    question_type: ETFQuestionType = ETFQuestionType.ETF_OVERVIEW,
    *,
    search: bool = True,
    reason: str = "latest_info",
    queries: list[str] | None = None,
    standalone: str = "What is the TER of the iShares Core S&P 500 UCITS ETF?",
) -> ETFRoutingResult:
    requirement = {
        ETFQuestionType.GENERAL_ETF: ETFDataRequirement.NONE,
        ETFQuestionType.ETF_OVERVIEW: ETFDataRequirement.BASIC,
        ETFQuestionType.ETF_DETAILED_ANALYSIS: ETFDataRequirement.DETAILED,
    }[question_type]
    if queries is None:
        queries = ["iShares Core S&P 500 UCITS ETF TER 2026"] if search else []
    return ETFRoutingResult(
        question_type=question_type,
        data_requirement=requirement,
        search_decision=SearchDecision(
            use_google_search=search,
            reason_code=reason,
            confidence=0.9,
            decision_model="router:test",
            decision_fallback="none",
        ),
        search_queries=queries,
        standalone_question=standalone,
    )


def _make(handler_cls, retriever: FakeRetriever | None = None, agent: FakeAgent | None = None):
    retriever = retriever or FakeRetriever()
    factory = FakeAgentFactory(agent)
    handler = handler_cls(
        retriever=retriever,
        brave_client=object(),
        agent_factory=factory,
        related_questions=two_related_questions,
    )
    return handler, retriever, factory.agent


async def _run(handler, *, retriever: FakeRetriever | None = None, **kwargs) -> list[dict]:
    params: dict[str, Any] = {
        "ticker": "SXR8",
        "question": "What is the TER?",
        "etf_data": make_etf(),
        "routing": _routing(),
        "preferred_model": ModelName.Auto,
        "request_id": "req-1",
    }
    params.update(kwargs)
    events: list[dict] = []
    if retriever is not None:
        retriever.events_seen_at_call = events  # type: ignore[attr-defined]
    async for event in handler.handle(**params):
        events.append(event)
    return events


def _types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


def _statuses(events: list[dict]) -> list[str]:
    return [e["body"] for e in events if e["type"] == "thinking_status"]


def _assert_tail_order(events: list[dict], *, with_sources: bool) -> None:
    types = _types(events)
    last_answer = max(i for i, t in enumerate(types) if t == "answer")
    model_idx = types.index("model_used")
    if with_sources:
        assert types.index("sources") == last_answer + 1
        assert model_idx == last_answer + 2
    else:
        assert "sources" not in types
        assert model_idx == last_answer + 1
    assert types[model_idx + 1 :] == ["related_question", "related_question"]


# --------------------------------------------------------------------------- general ETF


@pytest.mark.asyncio
async def test_general_search_on_retrieves_with_router_queries_and_grounds_on_sources():
    handler, retriever, agent = _make(GeneralETFHandlerV2)
    routing = _routing(ETFQuestionType.GENERAL_ETF, queries=["what is an ETF TER 2026"], standalone="What is a TER?")
    events = await _run(handler, retriever=retriever, routing=routing, question="What is a TER?", etf_data=None)

    (call,) = retriever.calls
    assert call["question"] == "What is a TER?"
    assert call["market"] == "GLOBAL"
    assert call["asset_class"] == "etf"
    assert call["company_name"] is None
    assert call["request_id"] == "req-1"
    assert call["query_reformulator"].reformulate("x", ticker="", company_name="").queries == [
        "what is an ETF TER 2026"
    ]

    statuses = _statuses(events)
    assert 'Searching for "what is an ETF TER 2026"...' in statuses
    assert any(s.startswith("Reading 1 sources") and "justETF" in s for s in statuses)

    prompt = agent.prompts[0]
    assert GROUNDING_RULES in prompt
    assert V1_SOURCE_MARKER not in prompt
    assert "Google Search" not in prompt
    assert "Passage from s_1" in prompt
    assert agent.search_flags == [False]

    assert [e["body"] for e in events if e["type"] == "answer"] == ["Alpha ", "Beta"]
    _assert_tail_order(events, with_sources=True)
    assert events[_types(events).index("model_used")]["body"] == "test-model"


@pytest.mark.asyncio
async def test_searching_status_is_sent_before_brave_is_called():
    seen: list[list[str]] = []

    class _Recording(FakeRetriever):
        def __call__(self, **kwargs):
            seen.append(_statuses(self.events_seen_at_call))  # type: ignore[attr-defined]
            return super().__call__(**kwargs)

    retriever = _Recording()
    handler, _, _ = _make(GeneralETFHandlerV2, retriever=retriever)
    await _run(handler, retriever=retriever, routing=_routing(ETFQuestionType.GENERAL_ETF), etf_data=None)
    assert seen and seen[0][-1].startswith('Searching for "')


@pytest.mark.asyncio
async def test_general_search_off_stable_concept_answers_from_knowledge():
    handler, retriever, agent = _make(GeneralETFHandlerV2)
    routing = _routing(ETFQuestionType.GENERAL_ETF, search=False, reason="stable_concept")
    events = await _run(handler, routing=routing, question="What is an ETF?", etf_data=None)

    assert retriever.calls == []
    assert "You may answer from your training knowledge" in agent.prompts[0]
    assert V1_SOURCE_MARKER not in agent.prompts[0]
    _assert_tail_order(events, with_sources=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["db_data_sufficient", "other"])
async def test_general_search_off_never_declines(reason):
    handler, retriever, agent = _make(GeneralETFHandlerV2)
    routing = _routing(ETFQuestionType.GENERAL_ETF, search=False, reason=reason)
    await _run(handler, routing=routing, etf_data=None, question="What is a TER?")
    assert retriever.calls == []
    assert NO_DATA_DECLINE not in agent.prompts[0]
    assert "You may answer from your training knowledge" in agent.prompts[0]


# --------------------------------------------------------------------------- overview


@pytest.mark.asyncio
async def test_overview_complete_data_and_search_off_skips_brave():
    handler, retriever, agent = _make(ETFOverviewHandlerV2)
    events = await _run(handler, routing=_routing(search=False, reason="db_data_sufficient"))

    assert retriever.calls == []
    prompt = agent.prompts[0]
    assert GROUNDING_RULES in prompt
    assert '"ter_percent": 0.07' in prompt
    assert V1_SOURCE_MARKER not in prompt
    assert "Google Search" not in prompt
    assert not any("searching online" in s for s in _statuses(events))
    _assert_tail_order(events, with_sources=False)


@pytest.mark.asyncio
async def test_overview_missing_core_data_forces_search_over_router_search_off():
    handler, retriever, agent = _make(ETFOverviewHandlerV2)
    etf = make_etf(ter_percent=None)
    events = await _run(handler, routing=_routing(search=False, reason="db_data_sufficient"), etf_data=etf)

    (call,) = retriever.calls
    queries = call["query_reformulator"].reformulate("x", ticker="", company_name="").queries
    # Router had no queries (search off): the handler builds one from the fund name + ticker.
    assert queries == ["iShares Core S&P 500 UCITS ETF SXR8 What is the TER of the iShares Core S&P 500 UCITS ETF?"]
    assert call["company_name"] == "iShares Core S&P 500 UCITS ETF"
    assert call["ticker"] == "SXR8"
    statuses = _statuses(events)
    assert any("Some SXR8 data missing" in s for s in statuses)
    assert f'Searching for "{queries[0]}"...' in statuses
    assert GROUNDING_RULES in agent.prompts[0]
    _assert_tail_order(events, with_sources=True)


@pytest.mark.asyncio
async def test_overview_without_db_row_forces_search_and_uses_ticker():
    handler, retriever, _ = _make(ETFOverviewHandlerV2)
    await _run(handler, routing=_routing(search=False, reason="db_data_sufficient"), etf_data=None)
    (call,) = retriever.calls
    assert call["company_name"] is None
    assert call["query_reformulator"].reformulate("x", ticker="", company_name="").queries[0].startswith("SXR8 ")


# --------------------------------------------------------------------------- detailed


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_field", ["holdings", "sector_allocation", "country_allocation"])
async def test_detailed_empty_arrays_force_search(empty_field):
    handler, retriever, agent = _make(ETFDetailedAnalysisHandlerV2)
    etf = make_etf(**{empty_field: []})
    routing = _routing(ETFQuestionType.ETF_DETAILED_ANALYSIS, search=False, reason="db_data_sufficient")
    events = await _run(handler, routing=routing, etf_data=etf, question="Top holdings?")

    assert len(retriever.calls) == 1
    prompt = agent.prompts[0]
    assert GROUNDING_RULES in prompt
    assert "search online" not in prompt
    assert V1_SOURCE_MARKER not in prompt
    _assert_tail_order(events, with_sources=True)


@pytest.mark.asyncio
async def test_detailed_full_data_and_search_off_skips_brave():
    handler, retriever, agent = _make(ETFDetailedAnalysisHandlerV2)
    routing = _routing(ETFQuestionType.ETF_DETAILED_ANALYSIS, search=False, reason="db_data_sufficient")
    events = await _run(handler, routing=routing, question="Top holdings?")

    assert retriever.calls == []
    assert '"name": "NVIDIA"' in agent.prompts[0]
    _assert_tail_order(events, with_sources=False)


@pytest.mark.asyncio
async def test_detailed_search_on_retrieves_and_streams_sources_before_model_used():
    handler, retriever, agent = _make(ETFDetailedAnalysisHandlerV2)
    routing = _routing(ETFQuestionType.ETF_DETAILED_ANALYSIS, queries=["iShares Core S&P 500 holdings 2026"])
    events = await _run(handler, routing=routing, question="Top holdings?")

    (call,) = retriever.calls
    assert call["question"] == routing.standalone_question
    assert call["asset_class"] == "etf"
    sources_event = next(e for e in events if e["type"] == "sources")
    assert sources_event["body"][0]["url"] == "https://www.justetf.com/en/etf-profile.html"
    assert "Sources:\nSource 1" in agent.prompts[0]
    _assert_tail_order(events, with_sources=True)


# --------------------------------------------------------------------------- shared behaviour


@pytest.mark.asyncio
async def test_conversation_context_and_debug_prompt_event():
    handler, _, agent = _make(ETFOverviewHandlerV2)
    history = [
        {"role": "user", "content": "Who issues SXR8?"},
        {"role": "assistant", "content": "iShares by BlackRock."},
    ]
    events = await _run(handler, conversation_messages=history, debug_prompt_context=True)

    assert "USER: Who issues SXR8?" in agent.prompts[0]
    debug = next(e for e in events if e["type"] == "debug_prompt_context")
    assert debug["body"]["handler"] == "etf_overview_v2"
    assert debug["body"]["prompt"] == agent.prompts[0]
    assert debug["body"]["source_count"] == 1
    assert _types(events).index("debug_prompt_context") < _types(events).index("answer")


@pytest.mark.asyncio
async def test_brave_failure_propagates_for_the_stream_service():
    retriever = FakeRetriever(result=BraveRetrievalError("down"))
    handler, _, agent = _make(ETFOverviewHandlerV2, retriever=retriever)
    with pytest.raises(BraveRetrievalError):
        await _run(handler)
    assert agent.prompts == []


@pytest.mark.asyncio
async def test_answer_failure_cancels_related_questions_task():
    started: list[bool] = []
    cancelled: list[bool] = []

    async def slow_related(*_args, **_kwargs):
        started.append(True)
        try:
            await asyncio.sleep(10)
        except BaseException:
            cancelled.append(True)
            raise
        yield {"type": "related_question", "body": "never"}

    class _Boom(FakeAgent):
        def generate_content(self, prompt: str, use_google_search: bool = False, **_kwargs):
            raise RuntimeError("llm down")

    handler = GeneralETFHandlerV2(
        retriever=FakeRetriever(),
        brave_client=object(),
        agent_factory=FakeAgentFactory(_Boom()),
        related_questions=slow_related,
    )
    with pytest.raises(RuntimeError):
        await _run(handler, routing=_routing(ETFQuestionType.GENERAL_ETF), etf_data=None)
    await asyncio.sleep(0)
    assert started == [True]
    assert cancelled == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_cls", "question_type", "etf"),
    [
        (ETFOverviewHandlerV2, ETFQuestionType.ETF_OVERVIEW, make_etf(ter_percent=None)),
        (ETFDetailedAnalysisHandlerV2, ETFQuestionType.ETF_DETAILED_ANALYSIS, make_etf(holdings=[])),
    ],
)
async def test_forced_search_failure_answers_from_the_database(handler_cls, question_type, etf):
    retriever = FakeRetriever(result=BraveRetrievalError("down"))
    handler, _, agent = _make(handler_cls, retriever=retriever)
    routing = _routing(question_type, search=False, reason="db_data_sufficient")

    events = await _run(handler, routing=routing, etf_data=etf)

    assert len(retriever.calls) == 1
    assert len(agent.prompts) == 1
    assert GROUNDING_RULES in agent.prompts[0]
    assert "Sources:\nSource 1" not in agent.prompts[0]
    assert "error" not in _types(events)
    assert any("answering from our database" in s for s in _statuses(events))
    _assert_tail_order(events, with_sources=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_cls", "question_type", "etf"),
    [
        (ETFOverviewHandlerV2, ETFQuestionType.ETF_OVERVIEW, make_etf(ter_percent=None)),
        (ETFDetailedAnalysisHandlerV2, ETFQuestionType.ETF_DETAILED_ANALYSIS, make_etf(holdings=[])),
        (GeneralETFHandlerV2, ETFQuestionType.GENERAL_ETF, None),
    ],
)
async def test_router_requested_search_failure_still_raises(handler_cls, question_type, etf):
    retriever = FakeRetriever(result=BraveRetrievalError("down"))
    handler, _, agent = _make(handler_cls, retriever=retriever)
    with pytest.raises(BraveRetrievalError):
        await _run(handler, routing=_routing(question_type, search=True), etf_data=etf)
    assert agent.prompts == []


LONG_QUESTION = "What does it say about " + " ".join(f"topic{i}" for i in range(120)) + "?"


@pytest.mark.asyncio
async def test_handler_built_query_is_capped_for_long_questions_and_names():
    handler, retriever, _ = _make(ETFOverviewHandlerV2)
    long_name = "iShares Core S&P 500 UCITS ETF " + "Extra Long Share Class Name " * 20
    routing = _routing(search=False, reason="db_data_sufficient", standalone=LONG_QUESTION)
    events = await _run(handler, routing=routing, etf_data=make_etf(name=long_name, ter_percent=None))

    (call,) = retriever.calls
    (query,) = call["query_reformulator"].reformulate("x", ticker="", company_name="").queries
    assert query == trim_search_query(query)
    assert len(query) <= MAX_QUERY_CHARS
    assert len(query.split()) <= MAX_QUERY_WORDS
    assert query.startswith("iShares Core S&P 500 UCITS ETF")
    assert f'Searching for "{query}"...' in _statuses(events)
