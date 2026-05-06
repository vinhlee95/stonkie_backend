from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ai_models.model_name import ModelName
from services.analyze_retrieval.schemas import (
    AnalyzeRetrievalResult,
    AnalyzeSource,
    BraveRetrievalError,
)
from services.search_decision_engine import SearchDecision


def _decision(use_search: bool) -> SearchDecision:
    return SearchDecision(
        use_google_search=use_search,
        reason_code="latest_info" if use_search else "stable_concept",
        confidence=0.9,
        decision_model="test",
        decision_fallback="none",
    )


def _src(sid: str, publisher: str, *, trusted=True) -> AnalyzeSource:
    return AnalyzeSource(
        id=sid,
        url=f"https://example.com/{sid}",
        title=f"title-{sid}",
        publisher=publisher,
        published_at=None,
        is_trusted=trusted,
    )


def _make_companies_mock():
    """Mock _fetch_companies_parallel to return DB-source CompanyComparisonData."""
    from services.question_analyzer.context_builders.comparison_builder import CompanyComparisonData

    def _factory(tickers):
        return [CompanyComparisonData(ticker=t, data_source="database") for t in tickers]

    return _factory


def _patch_fetch(handler, tickers):
    factory = _make_companies_mock()

    async def fake_fetch(self_, tickers_arg):
        return factory(tickers_arg)

    handler._fetch_companies_parallel = fake_fetch.__get__(handler, type(handler))


@pytest.mark.asyncio
@patch("services.question_analyzer.comparison_handler_v2.retrieve_for_analyze")
@patch("services.question_analyzer.comparison_handler_v2.MultiAgent")
async def test_search_on_passes_company_names_to_per_ticker_retrieval(mock_multi_agent_cls, mock_retrieve):
    from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2
    from services.question_analyzer.context_builders.comparison_builder import CompanyComparisonData

    mock_retrieve.side_effect = lambda **kw: AnalyzeRetrievalResult(
        sources=[_src(f"{kw['ticker']}_1", "Reuters")],
        query="q",
        market="GLOBAL",
        request_id=kw["ticker"],
    )

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["A"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanyComparisonHandlerV2()

    async def fake_fetch(self_, tickers_arg):
        _ = tickers_arg
        return [
            CompanyComparisonData(
                ticker="AAPL",
                fundamental=SimpleNamespace(
                    name="Apple Inc.",
                    sector="Technology",
                    industry="Consumer Electronics",
                    market_cap=1_000_000,
                    pe_ratio=25.0,
                    basic_eps=6.0,
                    revenue=500_000,
                    net_income=100_000,
                    dividend_yield=0.5,
                ),
                data_source="database",
            ),
            CompanyComparisonData(
                ticker="MSFT",
                fundamental=SimpleNamespace(
                    name="Microsoft Corporation",
                    sector="Technology",
                    industry="Software",
                    market_cap=2_000_000,
                    pe_ratio=30.0,
                    basic_eps=8.0,
                    revenue=700_000,
                    net_income=200_000,
                    dividend_yield=0.7,
                ),
                data_source="database",
            ),
        ]

    handler._fetch_companies_parallel = fake_fetch.__get__(handler, type(handler))

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    async for _event in handler.handle(
        tickers=["AAPL", "MSFT"],
        question="How do its margins compare?",
        search_decision=_decision(True),
        short_analysis=True,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="rq",
    ):
        pass

    assert [call.kwargs["company_name"] for call in mock_retrieve.call_args_list] == [
        "Apple Inc.",
        "Microsoft Corporation",
    ]


@pytest.mark.asyncio
@patch("services.question_analyzer.comparison_handler_v2.retrieve_for_analyze")
@patch("services.question_analyzer.comparison_handler_v2.MultiAgent")
async def test_per_ticker_brave_fanout_with_semaphore_cap_5(mock_multi_agent_cls, mock_retrieve):
    from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2

    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    mock_retrieve.side_effect = lambda **kw: AnalyzeRetrievalResult(
        sources=[_src(f"{kw['ticker']}_1", "Reuters")],
        query="q",
        market="GLOBAL",
        request_id=kw["ticker"],
    )

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["A"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanyComparisonHandlerV2()
    _patch_fetch(handler, ["A", "B", "C", "D", "E", "F", "G", "H"])

    # Inject a tracking wrapper around the per-ticker retrieval coroutine
    async def tracked(*, ticker, question, market, request_id, sem, company_name=None):
        _ = (question, market, request_id, company_name)
        async with sem:
            nonlocal in_flight, max_in_flight
            async with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.02)
            async with lock:
                in_flight -= 1
            return ticker, [_src(f"{ticker}_1", "Reuters")], None

    handler._retrieve_one_ticker = tracked  # type: ignore[assignment]

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        tickers=["A", "B", "C", "D", "E", "F", "G", "H"],
        question="compare",
        search_decision=_decision(True),
        short_analysis=True,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="rq",
    ):
        events.append(event)

    assert max_in_flight <= 5
    assert max_in_flight >= 1


@pytest.mark.asyncio
@patch("services.question_analyzer.comparison_handler_v2.retrieve_for_analyze")
@patch("services.question_analyzer.comparison_handler_v2.MultiAgent")
async def test_aggregated_thinking_status_across_tickers(mock_multi_agent_cls, mock_retrieve):
    from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2

    publishers_by_ticker = {
        "AAPL": [_src("aapl_1", "Reuters"), _src("aapl_2", "Bloomberg")],
        "MSFT": [_src("msft_1", "FT")],
        "GOOG": [_src("goog_1", "CNBC")],
    }

    def fake_retrieve(**kw):
        return AnalyzeRetrievalResult(
            sources=publishers_by_ticker[kw["ticker"]],
            query="q",
            market="GLOBAL",
            request_id=kw["ticker"],
        )

    mock_retrieve.side_effect = fake_retrieve

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["A"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanyComparisonHandlerV2()
    _patch_fetch(handler, ["AAPL", "MSFT", "GOOG"])

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        tickers=["AAPL", "MSFT", "GOOG"],
        question="compare",
        search_decision=_decision(True),
        short_analysis=True,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="rq",
    ):
        events.append(event)

    reading = [e for e in events if e["type"] == "thinking_status" and "Reading" in e["body"]]
    assert len(reading) == 1
    body = reading[0]["body"]
    for pub in ["Reuters", "Bloomberg", "FT", "CNBC"]:
        assert pub in body
    for ticker in ["AAPL", "MSFT", "GOOG"]:
        assert ticker in body


@pytest.mark.asyncio
@patch("services.question_analyzer.comparison_handler_v2.retrieve_for_analyze")
@patch("services.question_analyzer.comparison_handler_v2.MultiAgent")
async def test_partial_failure_proceeds_with_successes(mock_multi_agent_cls, mock_retrieve):
    from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2

    def fake_retrieve(**kw):
        if kw["ticker"] == "TSLA":
            raise BraveRetrievalError("brave 500")
        return AnalyzeRetrievalResult(
            sources=[_src(f"{kw['ticker']}_1", "Reuters")],
            query="q",
            market="GLOBAL",
            request_id=kw["ticker"],
        )

    mock_retrieve.side_effect = fake_retrieve

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["Comparison answer"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanyComparisonHandlerV2()
    _patch_fetch(handler, ["AAPL", "MSFT", "GOOG", "TSLA"])

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        tickers=["AAPL", "MSFT", "GOOG", "TSLA"],
        question="compare",
        search_decision=_decision(True),
        short_analysis=True,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="rq",
    ):
        events.append(event)

    failed_status = [
        e
        for e in events
        if e["type"] == "thinking_status"
        and "TSLA" in e["body"]
        and ("failed" in e["body"].lower() or "unavailable" in e["body"].lower())
    ]
    assert len(failed_status) >= 1

    sources_events = [e for e in events if e["type"] == "sources"]
    assert len(sources_events) == 1

    # Prompt context includes failure marker
    prompt = mock_agent.generate_content.call_args.kwargs["prompt"]
    assert "TSLA" in prompt
    assert "failed" in prompt.lower() or "unavailable" in prompt.lower()


@pytest.mark.asyncio
@patch("services.question_analyzer.comparison_handler_v2.retrieve_for_analyze")
@patch("services.question_analyzer.comparison_handler_v2.MultiAgent")
async def test_all_failures_raises_brave_retrieval_error(mock_multi_agent_cls, mock_retrieve):
    from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2

    mock_retrieve.side_effect = BraveRetrievalError("down")

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanyComparisonHandlerV2()
    _patch_fetch(handler, ["AAPL", "MSFT"])

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    with pytest.raises(BraveRetrievalError):
        async for _event in handler.handle(
            tickers=["AAPL", "MSFT"],
            question="compare",
            search_decision=_decision(True),
            short_analysis=True,
            preferred_model=ModelName.Auto,
            conversation_messages=None,
            request_id="rq",
        ):
            pass


@pytest.mark.asyncio
@patch("services.question_analyzer.comparison_handler_v2.retrieve_for_analyze")
@patch("services.question_analyzer.comparison_handler_v2.MultiAgent")
async def test_flat_source_id_numbering_across_tickers(mock_multi_agent_cls, mock_retrieve):
    from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2

    by_ticker = {
        "AAPL": [_src("aapl_1", "Reuters"), _src("aapl_2", "Bloomberg")],
        "MSFT": [_src("msft_1", "FT"), _src("msft_2", "CNBC")],
        "GOOG": [_src("goog_1", "WSJ"), _src("goog_2", "Barron")],
    }
    mock_retrieve.side_effect = lambda **kw: AnalyzeRetrievalResult(
        sources=by_ticker[kw["ticker"]],
        query="q",
        market="GLOBAL",
        request_id=kw["ticker"],
    )

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["[1] [2] [3] [4] [5] [6]"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanyComparisonHandlerV2()
    _patch_fetch(handler, ["AAPL", "MSFT", "GOOG"])

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        tickers=["AAPL", "MSFT", "GOOG"],
        question="compare",
        search_decision=_decision(True),
        short_analysis=True,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="rq",
    ):
        events.append(event)

    sources_events = [e for e in events if e["type"] == "sources"]
    assert len(sources_events) == 1
    ids = [s["source_id"] for s in sources_events[0]["body"]]
    assert ids == ["aapl_1", "aapl_2", "msft_1", "msft_2", "goog_1", "goog_2"]


@pytest.mark.asyncio
@patch("services.question_analyzer.comparison_handler_v2.MultiAgent")
async def test_no_search_path_runs_v1_like_comparison(mock_multi_agent_cls):
    from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["A"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanyComparisonHandlerV2()
    _patch_fetch(handler, ["AAPL", "MSFT"])

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        tickers=["AAPL", "MSFT"],
        question="compare",
        search_decision=_decision(False),
        short_analysis=True,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="rq",
    ):
        events.append(event)

    # No "Reading N sources" thinking_status (no Brave)
    assert not any(e["type"] == "thinking_status" and "Reading" in e["body"] for e in events)
    # No v2 sources event from build_sources_event
    v2_sources = [e for e in events if e["type"] == "sources" and isinstance(e.get("body"), list)]
    assert len(v2_sources) == 0


@pytest.mark.asyncio
@patch("services.question_analyzer.comparison_handler_v2.retrieve_for_analyze")
@patch("services.question_analyzer.comparison_handler_v2.MultiAgent")
async def test_search_on_emits_all_retrieved_sources_regardless_of_inline_citations(
    mock_multi_agent_cls, mock_retrieve
):
    from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2

    mock_retrieve.side_effect = lambda **kw: AnalyzeRetrievalResult(
        sources=[_src(f"{kw['ticker']}_1", "Reuters")],
        query="q",
        market="GLOBAL",
        request_id=kw["ticker"],
    )
    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["No citations here."])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanyComparisonHandlerV2()
    _patch_fetch(handler, ["AAPL", "MSFT"])

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        tickers=["AAPL", "MSFT"],
        question="compare",
        search_decision=_decision(True),
        short_analysis=True,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="rq",
    ):
        events.append(event)

    v2_sources = [e for e in events if e["type"] == "sources" and isinstance(e.get("body"), list)]
    assert len(v2_sources) == 1
    assert {s["source_id"] for s in v2_sources[0]["body"]} == {"AAPL_1", "MSFT_1"}


@pytest.mark.asyncio
@patch("services.question_analyzer.comparison_handler_v2.retrieve_for_analyze")
@patch("services.question_analyzer.comparison_handler_v2.MultiAgent")
async def test_dedupes_trusted_publishers_across_tickers(mock_multi_agent_cls, mock_retrieve):
    from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2

    mock_retrieve.side_effect = lambda **kw: AnalyzeRetrievalResult(
        sources=[_src(f"{kw['ticker']}_1", "Reuters")],
        query="q",
        market="GLOBAL",
        request_id=kw["ticker"],
    )
    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["A"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanyComparisonHandlerV2()
    _patch_fetch(handler, ["AAPL", "MSFT", "GOOG"])

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        tickers=["AAPL", "MSFT", "GOOG"],
        question="compare",
        search_decision=_decision(True),
        short_analysis=True,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="rq",
    ):
        events.append(event)

    reading = [e for e in events if e["type"] == "thinking_status" and "Reading" in e["body"]][0]
    assert reading["body"].count("Reuters") == 1


@pytest.mark.asyncio
@patch("services.question_analyzer.comparison_handler_v2.retrieve_for_analyze")
@patch("services.question_analyzer.comparison_handler_v2.MultiAgent")
async def test_comparison_prompt_drops_sources_json_instructions_for_v2(mock_multi_agent_cls, mock_retrieve):
    from services.question_analyzer.comparison_handler_v2 import CompanyComparisonHandlerV2
    from services.question_analyzer.context_builders.components import PromptComponents

    mock_retrieve.side_effect = lambda **kw: AnalyzeRetrievalResult(
        sources=[_src(f"{kw['ticker']}_1", "Reuters")],
        query="q",
        market="GLOBAL",
        request_id=kw["ticker"],
    )
    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["A [1]"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanyComparisonHandlerV2()
    _patch_fetch(handler, ["AAPL", "MSFT"])

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    async for _event in handler.handle(
        tickers=["AAPL", "MSFT"],
        question="compare",
        search_decision=_decision(True),
        short_analysis=True,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="rq",
    ):
        pass

    prompt_text = mock_agent.generate_content.call_args.kwargs.get("prompt", "")
    assert PromptComponents.grounding_rules() in prompt_text
    assert '[SOURCES_JSON]{"sources"' not in prompt_text
    assert "ALL citations must appear exclusively inside [SOURCES_JSON]" not in prompt_text
