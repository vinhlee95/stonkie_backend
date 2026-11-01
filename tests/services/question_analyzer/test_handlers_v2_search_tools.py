"""Always-on search, the web_search tool and narrative prompts in the three stock v2 handlers (fakes, no network)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_models.model_name import ModelName
from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeRetrievalResult, AnalyzeSource
from services.question_analyzer.context_builders.components import PromptComponents
from services.question_analyzer.handlers_v2 import (
    CompanyGeneralHandlerV2,
    CompanySpecificFinanceHandlerV2,
    GeneralFinanceHandlerV2,
)
from services.question_analyzer.types import FinancialDataRequirement, FinancialPeriodRequirement, QuestionType
from tests.services.routing_fakes import make_routing

_HANDLERS = "services.question_analyzer.handlers_v2"


def _source(source_id: str, publisher: str = "Reuters") -> AnalyzeSource:
    return AnalyzeSource(
        id=source_id,
        url=f"https://example.com/{source_id}",
        title=f"Title {source_id}",
        publisher=publisher,
        is_trusted=True,
    )


def _retrieval(*sources: AnalyzeSource) -> AnalyzeRetrievalResult:
    return AnalyzeRetrievalResult(
        sources=list(sources),
        selected_passages=[
            AnalyzePassage(
                source_id=s.id,
                url=s.url,
                title=s.title,
                publisher=s.publisher,
                is_trusted=True,
                passage_index=0,
                content=f"About {s.id}",
            )
            for s in sources
        ],
        query="q",
        market="GLOBAL",
        request_id="r",
    )


async def _no_related(*_a, **_k):
    if False:
        yield {}


def _agent(stream_for_call):
    """MultiAgent whose generate_content records kwargs and runs `stream_for_call(kwargs)`."""
    agent = MagicMock()
    agent.model_name = "test-model"
    agent.served_model_name = "test-model"
    agent.calls = []

    def generate_content(**kwargs):
        agent.calls.append(kwargs)
        return iter(stream_for_call(kwargs))

    agent.generate_content.side_effect = generate_content
    return agent


def _plain_answer(_kwargs):
    return ["Answer."]


def _specific(quarterly=None):
    optimizer = MagicMock()
    optimizer.fetch_optimized_data = AsyncMock(
        return_value=({"Name": "Alphabet Inc."}, [], quarterly or [{"period_end_quarter": "6/30/2026"}])
    )
    connector = MagicMock()
    connector.get_by_ticker.return_value = SimpleNamespace(name="Alphabet Inc.", country="United States")
    handler = CompanySpecificFinanceHandlerV2(company_connector=connector, data_optimizer=optimizer)
    handler._generate_related_questions = _no_related  # type: ignore[method-assign]
    return handler


def _specific_routing():
    return make_routing(
        QuestionType.COMPANY_SPECIFIC_FINANCE.value,
        use_search=False,
        data_requirement=FinancialDataRequirement.DETAILED,
        period_requirement=FinancialPeriodRequirement(period_type="quarterly", num_periods=4),
        standalone_question="What's Alphabet's profit margin trend?",
    )


def _general_company():
    connector = MagicMock()
    connector.get_by_ticker.return_value = SimpleNamespace(name="Tesla, Inc.", country="United States")
    handler = CompanyGeneralHandlerV2(company_connector=connector)
    handler._generate_related_questions = _no_related  # type: ignore[method-assign]
    return handler


def _general_finance():
    handler = GeneralFinanceHandlerV2()
    handler._generate_related_questions = _no_related  # type: ignore[method-assign]
    return handler


async def _run(handler_name: str, *, question="q", use_url_context=False):
    if handler_name == "specific":
        routing = _specific_routing()
        gen = _specific().handle(
            ticker="GOOG",
            question=question,
            search_decision=routing.search_decision,
            use_url_context=use_url_context,
            preferred_model=ModelName.Auto,
            routing=routing,
        )
    elif handler_name == "company_general":
        routing = make_routing(QuestionType.COMPANY_GENERAL.value, use_search=False, standalone_question=question)
        gen = _general_company().handle(
            ticker="TSLA",
            question=question,
            search_decision=routing.search_decision,
            use_url_context=use_url_context,
            preferred_model=ModelName.Auto,
            routing=routing,
        )
    else:
        routing = make_routing(QuestionType.GENERAL_FINANCE.value, use_search=False, standalone_question=question)
        gen = _general_finance().handle(
            question=question,
            search_decision=routing.search_decision,
            use_url_context=use_url_context,
            preferred_model=ModelName.Auto,
            routing=routing,
        )
    return [event async for event in gen]


def _statuses(events):
    return [e["body"] for e in events if e["type"] == "thinking_status"]


_ALL = ["specific", "company_general", "general_finance"]


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_name", _ALL)
async def test_search_runs_when_router_says_no_search(handler_name):
    retrieve = MagicMock(return_value=_retrieval(_source("a")))
    agent = _agent(_plain_answer)
    with patch(f"{_HANDLERS}.retrieve_for_analyze", retrieve), patch(f"{_HANDLERS}.MultiAgent", return_value=agent):
        events = await _run(handler_name)

    assert retrieve.called
    assert any(e["type"] == "sources" for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "status"),
    [
        ("specific", "No relevant web results, analyzing GOOG financials..."),
        ("company_general", "No relevant web results, answering from the available data..."),
        ("general_finance", "No relevant web results, answering from the available data..."),
    ],
)
async def test_brave_error_on_initial_search_still_answers(handler_name, status):
    agent = _agent(_plain_answer)
    with patch(f"{_HANDLERS}.MultiAgent", return_value=agent):  # conftest: retrieval raises BraveRetrievalError
        events = await _run(handler_name)

    assert status in _statuses(events)
    assert all(e["type"] != "error" for e in events)
    assert [e["body"] for e in events if e["type"] == "answer"] == ["Answer."]


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_name", _ALL)
async def test_generate_content_receives_web_search_tool(handler_name):
    agent = _agent(_plain_answer)
    with patch(f"{_HANDLERS}.MultiAgent", return_value=agent):
        await _run(handler_name)

    kwargs = agent.calls[0]
    assert kwargs["tools"][0].name == "web_search"
    assert kwargs["max_tool_rounds"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_name", _ALL)
async def test_prompt_has_narrative_format_and_search_instruction(handler_name):
    agent = _agent(_plain_answer)
    with patch(f"{_HANDLERS}.MultiAgent", return_value=agent):
        await _run(handler_name)

    prompt = agent.calls[0]["prompt"]
    assert PromptComponents.narrative_answer_format() in prompt
    assert PromptComponents.search_instruction() in prompt
    assert "under 150 words" not in prompt and "under 200 words" not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_name", _ALL)
async def test_sources_footer_includes_tool_sources(handler_name):
    def tool_using_stream(kwargs):
        tool = kwargs["tools"][0]
        result = tool.run({"query": "Alphabet Q2 2026 other income"})
        return [
            {"type": "tool_call", "name": "web_search", "arguments": {"query": "Alphabet Q2 2026 other income"}},
            {"type": "tool_result", "name": "web_search", "arguments": {"query": "x"}, "ok": True, "meta": result.meta},
            "Answer.",
        ]

    def retrieve(**kwargs):
        if kwargs["question"] == "Alphabet Q2 2026 other income":
            return _retrieval(_source("tool-src", publisher="CNBC"))
        return _retrieval(_source("initial-src"))

    agent = _agent(tool_using_stream)
    with (
        patch(f"{_HANDLERS}.retrieve_for_analyze", side_effect=retrieve),
        patch(f"{_HANDLERS}.MultiAgent", return_value=agent),
    ):
        events = await _run(handler_name)

    footer = next(e for e in events if e["type"] == "sources")
    urls = [s["url"] for s in footer["body"]]
    assert "https://example.com/initial-src" in urls and "https://example.com/tool-src" in urls
    writing = next(b for b in _statuses(events) if b.lower().startswith("writing your answer"))
    assert "2 sources" in writing


@pytest.mark.asyncio
async def test_status_sequence_end_to_end():
    def tool_using_stream(kwargs):
        result = kwargs["tools"][0].run({"query": "Alphabet other income"})
        return [
            {"type": "tool_call", "name": "web_search", "arguments": {"query": "Alphabet other income"}},
            {
                "type": "tool_result",
                "name": "web_search",
                "arguments": {"query": "Alphabet other income"},
                "ok": True,
                "meta": result.meta,
            },
            "Answer.",
        ]

    def retrieve(**kwargs):
        if kwargs["question"] == "Alphabet other income":
            return _retrieval(_source("tool-src", publisher="CNBC"))
        return _retrieval(_source("initial-src"))

    agent = _agent(tool_using_stream)
    with (
        patch(f"{_HANDLERS}.retrieve_for_analyze", side_effect=retrieve),
        patch(f"{_HANDLERS}.MultiAgent", return_value=agent),
    ):
        events = await _run("specific")

    statuses = [e for e in events if e["type"] == "thinking_status"]
    # "Analyzing GOOG financials..." appears only when Brave is still running after the DB fetch (timing).
    assert [s["body"] for s in statuses if s["body"] != "Analyzing GOOG financials..."] == [
        "Searching for \"What's Alphabet's profit margin trend?\"...",
        "Checking what's behind the numbers...",
        'Digging deeper: "Alphabet other income"...',
        'Found 1 source on "Alphabet other income": CNBC',
        "Writing your answer from 2 sources: Reuters, CNBC...",
    ]
    assert [s["step"] for s in statuses] == list(range(1, len(statuses) + 1))


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_name", ["company_general", "general_finance"])
async def test_url_grounded_answer_has_no_tools(handler_name):
    ingest = SimpleNamespace(source=_source("doc"), selected_passages=[])
    agent = _agent(_plain_answer)
    with (
        patch(f"{_HANDLERS}._ingest_user_url", return_value=ingest),
        patch(f"{_HANDLERS}.MultiAgent", return_value=agent),
    ):
        await _run(handler_name, question="Summarize https://example.com/report.pdf", use_url_context=True)

    assert "tools" not in agent.calls[0]
    assert PromptComponents.search_instruction() not in agent.calls[0]["prompt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_name", ["company_general", "general_finance"])
async def test_no_initial_sources_asks_to_search_before_declining(handler_name):
    agent = _agent(_plain_answer)
    with patch(f"{_HANDLERS}.MultiAgent", return_value=agent):  # conftest: initial search finds nothing
        await _run(handler_name)

    prompt = agent.calls[0]["prompt"]
    assert PromptComponents.no_sources_search_first() in prompt
    assert PromptComponents.no_data_decline() not in prompt
