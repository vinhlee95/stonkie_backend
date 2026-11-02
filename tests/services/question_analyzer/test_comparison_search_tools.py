"""Comparison handler: always-on search, per-ticker web_search budget, narrative prompt (fakes, no network)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ai_models.model_name import ModelName
from services.analyze_retrieval.schemas import AnalyzeRetrievalResult, AnalyzeSource
from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2
from services.question_analyzer.context_builders.comparison_builder import CompanyComparisonData
from services.question_analyzer.context_builders.components import PromptComponents
from services.question_analyzer.types import QuestionType
from tests.services.routing_fakes import make_routing

_COMPARISON = "services.question_analyzer.comparison_handler_v2"


async def _no_related(*_a, **_k):
    if False:
        yield {}


def _agent():
    agent = MagicMock()
    agent.model_name = "test-model"
    agent.served_model_name = "test-model"
    agent.generate_content.side_effect = lambda **_kwargs: iter(["Answer."])
    return agent


def _handler():
    handler = CompanyComparisonHandlerV2(company_connector=MagicMock(), financial_connector=MagicMock())

    async def fake_fetch(tickers):
        return [CompanyComparisonData(ticker=t, data_source="database") for t in tickers]

    handler._fetch_companies_parallel = fake_fetch  # type: ignore[method-assign]
    handler._generate_related_questions = _no_related  # type: ignore[method-assign]
    return handler


async def _run(tickers):
    routing = make_routing(QuestionType.COMPANY_COMPARISON.value, use_search=False)
    return [
        e
        async for e in _handler().handle(
            tickers=tickers,
            question="How do Nvidia's margins compare with AMD?",
            search_decision=routing.search_decision,
            short_analysis=True,
            preferred_model=ModelName.Auto,
            routing=routing,
        )
    ]


def _retrieval():
    source = AnalyzeSource(id="s", url="https://reuters.com/s", title="s", publisher="Reuters", is_trusted=True)
    return AnalyzeRetrievalResult(sources=[source], selected_passages=[], query="q", market="GLOBAL", request_id="r")


@pytest.mark.asyncio
async def test_comparison_searches_when_router_says_no_search():
    retrieve = MagicMock(return_value=_retrieval())
    with (
        patch(f"{_COMPARISON}.retrieve_for_analyze", retrieve),
        patch(f"{_COMPARISON}.MultiAgent", return_value=_agent()),
    ):
        events = await _run(["NVDA", "AMD"])

    assert retrieve.call_count == 2
    assert any(e["type"] == "sources" for e in events)


@pytest.mark.asyncio
async def test_comparison_all_retrieval_failed_still_answers():
    with patch(f"{_COMPARISON}.MultiAgent", return_value=_agent()):  # conftest: retrieval raises
        events = await _run(["NVDA", "AMD"])

    statuses = [e["body"] for e in events if e["type"] == "thinking_status"]
    assert "No relevant web results for NVDA, AMD, using their financials..." in statuses
    assert all(e["type"] != "error" for e in events)
    assert [e["body"] for e in events if e["type"] == "answer"] == ["Answer."]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tickers", "rounds"), [(["NVDA", "AMD"], 4), (["NVDA", "AMD", "INTC"], 4)])
async def test_comparison_tool_budget_is_per_ticker_up_to_a_cap(tickers, rounds):
    agent = _agent()
    with patch(f"{_COMPARISON}.MultiAgent", return_value=agent):
        await _run(tickers)

    kwargs = agent.generate_content.call_args.kwargs
    assert kwargs["tools"][0].name == "web_search"
    assert kwargs["max_tool_rounds"] == rounds


@pytest.mark.asyncio
async def test_comparison_prompt_has_narrative_format_and_search_instruction():
    agent = _agent()
    with patch(f"{_COMPARISON}.MultiAgent", return_value=agent):
        await _run(["NVDA", "AMD"])

    prompt = agent.generate_content.call_args.kwargs["prompt"]
    assert PromptComponents.narrative_answer_format() in prompt
    assert PromptComponents.search_instruction() in prompt


@pytest.mark.asyncio
async def test_comparison_status_holds_checking_before_writing():
    with (
        patch(f"{_COMPARISON}.retrieve_for_analyze", MagicMock(return_value=_retrieval())),
        patch(f"{_COMPARISON}.MultiAgent", return_value=_agent()),
    ):
        events = await _run(["NVDA", "AMD"])

    statuses = [e["body"] for e in events if e["type"] == "thinking_status"]
    assert statuses[-2:] == [
        "Checking what's behind the numbers...",
        "Writing your answer from 1 source across NVDA, AMD: Reuters...",
    ]
