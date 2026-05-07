from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_models.model_name import ModelName
from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeRetrievalResult, AnalyzeSource
from services.analyze_retrieval.url_ingest import UrlIngestError, UrlIngestResult
from services.question_analyzer.types import FinancialDataRequirement
from services.search_decision_engine import SearchDecision


def _decision(use_search: bool) -> SearchDecision:
    return SearchDecision(
        use_google_search=use_search,
        reason_code="latest_info" if use_search else "stable_concept",
        confidence=0.9,
        decision_model="test",
        decision_fallback="none",
    )


_UNSET = object()


def _make_handler_deps(
    *, data_requirement=FinancialDataRequirement.BASIC, period=None, fundamental=_UNSET, annual=None, quarterly=None
):
    company_connector = MagicMock()
    company_connector.get_by_ticker.return_value = SimpleNamespace(name="Apple Inc.", country="United States")
    classifier = MagicMock()
    classifier.classify_data_and_period_requirement = AsyncMock(return_value=(data_requirement, period, None))
    optimizer = MagicMock()
    fundamental_value = {"Name": "Apple Inc."} if fundamental is _UNSET else fundamental
    optimizer.fetch_optimized_data = AsyncMock(return_value=(fundamental_value, annual or [], quarterly or []))
    return company_connector, classifier, optimizer


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_no_search_emits_v1_like_sequence(mock_multi_agent_cls):
    from services.question_analyzer.handlers_v2 import CompanySpecificFinanceHandlerV2

    company_connector, classifier, optimizer = _make_handler_deps()

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["Alpha ", "Beta"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanySpecificFinanceHandlerV2(
        company_connector=company_connector,
        classifier=classifier,
        data_optimizer=optimizer,
    )

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        ticker="AAPL",
        question="What's Apple's revenue?",
        search_decision=_decision(False),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
    ):
        events.append(event)

    thinking_bodies = [e["body"] for e in events if e["type"] == "thinking_status"]
    assert any("data you need" in b for b in thinking_bodies)
    assert any("Analyzing AAPL" in b for b in thinking_bodies)
    assert all(e["type"] != "sources" for e in events)
    assert [e["type"] for e in events].count("answer") == 2


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_no_search_never_emits_sources_event(mock_multi_agent_cls):
    from services.question_analyzer.handlers_v2 import CompanySpecificFinanceHandlerV2

    company_connector, classifier, optimizer = _make_handler_deps()
    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["Answer"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanySpecificFinanceHandlerV2(
        company_connector=company_connector,
        classifier=classifier,
        data_optimizer=optimizer,
    )

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        ticker="AAPL",
        question="q",
        search_decision=_decision(False),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
    ):
        events.append(event)

    assert all(e["type"] != "sources" for e in events)


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.retrieve_for_analyze")
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_search_on_emits_trusted_publishers_and_final_sources_once(mock_multi_agent_cls, mock_retrieve):
    from services.question_analyzer.handlers_v2 import CompanySpecificFinanceHandlerV2

    company_connector, classifier, optimizer = _make_handler_deps()
    sources = [
        AnalyzeSource(
            id="s_1",
            url="https://www.reuters.com/a",
            title="A",
            publisher="Reuters",
            published_at=None,
            is_trusted=True,
        ),
        AnalyzeSource(
            id="s_2",
            url="https://example-blog.com/b",
            title="B",
            publisher="Example Blog",
            published_at=None,
            is_trusted=False,
        ),
    ]
    mock_retrieve.return_value = AnalyzeRetrievalResult(
        sources=sources,
        query="q",
        market="GLOBAL",
        request_id="req-1",
    )

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["Alpha", " beta"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanySpecificFinanceHandlerV2(
        company_connector=company_connector,
        classifier=classifier,
        data_optimizer=optimizer,
    )

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        ticker="AAPL",
        question="What changed?",
        search_decision=_decision(True),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="req-1",
    ):
        events.append(event)

    publisher_status = [e for e in events if e["type"] == "thinking_status" and "Reading" in e["body"]]
    assert len(publisher_status) == 1
    assert "Reuters" in publisher_status[0]["body"]
    assert "Example Blog" not in publisher_status[0]["body"]

    answer_events = [e for e in events if e["type"] == "answer"]
    assert "".join(event["body"] for event in answer_events) == "Alpha beta"

    sources_events = [e for e in events if e["type"] == "sources"]
    assert len(sources_events) == 1
    assert [s["source_id"] for s in sources_events[0]["body"]] == ["s_1", "s_2"]
    prompt = mock_agent.generate_content.call_args.kwargs["prompt"]
    assert "Use ONLY the Source passages below to answer this URL-grounded question." not in prompt


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.retrieve_for_analyze")
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_search_on_dedupes_trusted_publishers(mock_multi_agent_cls, mock_retrieve):
    from services.question_analyzer.handlers_v2 import CompanySpecificFinanceHandlerV2

    company_connector, classifier, optimizer = _make_handler_deps()
    mock_retrieve.return_value = AnalyzeRetrievalResult(
        sources=[
            AnalyzeSource(
                id="s_1",
                url="https://www.reuters.com/a",
                title="A",
                publisher="Reuters",
                published_at=None,
                is_trusted=True,
            ),
            AnalyzeSource(
                id="s_2",
                url="https://www.reuters.com/b",
                title="B",
                publisher="Reuters",
                published_at=None,
                is_trusted=True,
            ),
        ],
        query="q",
        market="GLOBAL",
        request_id="req-2",
    )
    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["A [1]"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanySpecificFinanceHandlerV2(
        company_connector=company_connector,
        classifier=classifier,
        data_optimizer=optimizer,
    )

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        ticker="AAPL",
        question="q",
        search_decision=_decision(True),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="req-2",
    ):
        events.append(event)

    publisher_status = [e for e in events if e["type"] == "thinking_status" and "Reading" in e["body"]][0]["body"]
    assert publisher_status.count("Reuters") == 1


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.retrieve_for_analyze")
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_search_on_emits_all_retrieved_sources_regardless_of_inline_citations(
    mock_multi_agent_cls, mock_retrieve
):
    from services.question_analyzer.handlers_v2 import CompanySpecificFinanceHandlerV2

    company_connector, classifier, optimizer = _make_handler_deps()
    mock_retrieve.return_value = AnalyzeRetrievalResult(
        sources=[
            AnalyzeSource(
                id="s_1",
                url="https://www.reuters.com/a",
                title="A",
                publisher="Reuters",
                published_at=None,
                is_trusted=True,
            ),
        ],
        query="q",
        market="GLOBAL",
        request_id="req-3",
    )
    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["Answer no citations."])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanySpecificFinanceHandlerV2(
        company_connector=company_connector,
        classifier=classifier,
        data_optimizer=optimizer,
    )

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        ticker="AAPL",
        question="q",
        search_decision=_decision(True),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="req-3",
    ):
        events.append(event)

    sources_events = [e for e in events if e["type"] == "sources"]
    assert len(sources_events) == 1
    assert [s["source_id"] for s in sources_events[0]["body"]] == ["s_1"]


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.MultiAgent")
@patch("services.question_analyzer.handlers_v2.ingest_url")
async def test_quarterly_report_uses_tavily_ingest_sources(mock_ingest_url, mock_multi_agent_cls):
    from services.question_analyzer.handlers_v2 import CompanySpecificFinanceHandlerV2

    source = AnalyzeSource(
        id="s_q",
        url="https://sec.gov/aapl-q3.pdf",
        title="AAPL Q3 10-Q",
        publisher="SEC",
        is_trusted=True,
        raw_content="Revenue grew 6%.",
    )
    passage = AnalyzePassage(
        source_id="s_q",
        url=source.url,
        title=source.title,
        publisher=source.publisher,
        is_trusted=True,
        passage_index=1,
        content="Revenue grew 6%.",
    )
    mock_ingest_url.return_value = UrlIngestResult(sources=[source], selected_passages=[passage])
    company_connector, classifier, optimizer = _make_handler_deps(
        data_requirement=FinancialDataRequirement.QUARTERLY_SUMMARY,
        quarterly=[
            {
                "filing_10q_url": "https://sec.gov/aapl-q3.pdf",
                "period_end_quarter": "2024-Q3",
            }
        ],
    )
    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["A [1]"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanySpecificFinanceHandlerV2(
        company_connector=company_connector,
        classifier=classifier,
        data_optimizer=optimizer,
    )

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        ticker="AAPL",
        question="latest 10Q?",
        search_decision=_decision(False),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="req-q",
        debug_prompt_context=True,
    ):
        events.append(event)

    attachments = [e for e in events if e["type"] == "attachment_url"]
    assert len(attachments) == 1
    assert attachments[0]["body"] == "https://sec.gov/aapl-q3.pdf"
    mock_ingest_url.assert_called_once_with(
        url="https://sec.gov/aapl-q3.pdf",
        question="latest 10Q?",
        request_id="req-q",
        source_kind="filing",
    )
    mock_agent.generate_content.assert_called_once()
    prompt = mock_agent.generate_content.call_args.kwargs["prompt"]
    assert "Revenue grew 6%." in prompt
    assert "Use ONLY the Source passages below to answer this URL-grounded question." in prompt
    assert "database financial statements" in prompt
    assert any(e["type"] == "debug_prompt_context" for e in events)
    sources_events = [e for e in events if e["type"] == "sources"]
    assert len(sources_events) == 1
    assert sources_events[0]["body"][0]["url"] == "https://sec.gov/aapl-q3.pdf"


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.MultiAgent")
@patch("services.question_analyzer.handlers_v2.ingest_url")
async def test_annual_report_uses_tavily_ingest_sources(mock_ingest_url, mock_multi_agent_cls):
    from services.question_analyzer.handlers_v2 import CompanySpecificFinanceHandlerV2

    source = AnalyzeSource(
        id="s_k",
        url="https://sec.gov/aapl-2024.pdf",
        title="AAPL 2024 10-K",
        publisher="SEC",
        is_trusted=True,
        raw_content="Risk factors include demand volatility.",
    )
    passage = AnalyzePassage(
        source_id="s_k",
        url=source.url,
        title=source.title,
        publisher=source.publisher,
        is_trusted=True,
        passage_index=1,
        content="Risk factors include demand volatility.",
    )
    mock_ingest_url.return_value = UrlIngestResult(sources=[source], selected_passages=[passage])
    company_connector, classifier, optimizer = _make_handler_deps(
        data_requirement=FinancialDataRequirement.ANNUAL_SUMMARY,
        annual=[
            {
                "filing_10k_url": "https://sec.gov/aapl-2024.pdf",
                "period_end_year": "2024",
            }
        ],
    )
    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["A [1]"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanySpecificFinanceHandlerV2(
        company_connector=company_connector,
        classifier=classifier,
        data_optimizer=optimizer,
    )

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        ticker="AAPL",
        question="latest 10K?",
        search_decision=_decision(False),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="req-k",
    ):
        events.append(event)

    attachments = [e for e in events if e["type"] == "attachment_url"]
    assert len(attachments) == 1
    assert attachments[0]["body"] == "https://sec.gov/aapl-2024.pdf"
    mock_ingest_url.assert_called_once_with(
        url="https://sec.gov/aapl-2024.pdf",
        question="latest 10K?",
        request_id="req-k",
        source_kind="filing",
    )
    prompt = mock_agent.generate_content.call_args.kwargs["prompt"]
    assert "Risk factors include demand volatility." in prompt
    assert "Use ONLY the Source passages below to answer this URL-grounded question." in prompt
    assert "database financial statements" in prompt
    assert [e for e in events if e["type"] == "sources"][0]["body"][0]["source_id"] == "s_k"


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.MultiAgent")
@patch("services.question_analyzer.handlers_v2.ingest_url")
async def test_report_ingest_failure_emits_error_and_skips_llm(mock_ingest_url, mock_multi_agent_cls):
    from services.question_analyzer.handlers_v2 import CompanySpecificFinanceHandlerV2

    mock_ingest_url.side_effect = UrlIngestError("no chunks")
    company_connector, classifier, optimizer = _make_handler_deps(
        data_requirement=FinancialDataRequirement.QUARTERLY_SUMMARY,
        quarterly=[
            {
                "filing_10q_url": "https://sec.gov/aapl-q3.pdf",
                "period_end_quarter": "2024-Q3",
            }
        ],
    )
    handler = CompanySpecificFinanceHandlerV2(
        company_connector=company_connector,
        classifier=classifier,
        data_optimizer=optimizer,
    )

    events: list[dict] = []
    async for event in handler.handle(
        ticker="AAPL",
        question="latest 10Q?",
        search_decision=_decision(False),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="req-fail",
    ):
        events.append(event)

    assert any(e["type"] == "attachment_url" for e in events)
    assert events[-1] == {
        "type": "error",
        "code": "url_ingest_failed",
        "body": "Could not read the document URL.",
    }
    mock_multi_agent_cls.assert_not_called()


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_fallback_undefined_ticker_with_conversation(mock_multi_agent_cls):
    from services.question_analyzer.handlers_v2 import CompanySpecificFinanceHandlerV2

    company_connector, classifier, optimizer = _make_handler_deps()
    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["Fallback answer"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanySpecificFinanceHandlerV2(
        company_connector=company_connector,
        classifier=classifier,
        data_optimizer=optimizer,
    )

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        ticker="undefined",
        question="based on that, what's next?",
        search_decision=_decision(True),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=[
            {"role": "user", "content": "Tell me about Apple's revenue."},
            {"role": "assistant", "content": "Apple revenue is $400B."},
        ],
    ):
        events.append(event)

    classifier.classify_data_and_period_requirement.assert_not_called()
    optimizer.fetch_optimized_data.assert_not_called()
    assert [e["type"] for e in events].count("answer") == 1
    assert all(e["type"] != "sources" for e in events)


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_fallback_no_db_data_with_conversation(mock_multi_agent_cls):
    from services.question_analyzer.handlers_v2 import CompanySpecificFinanceHandlerV2

    company_connector, classifier, optimizer = _make_handler_deps(
        data_requirement=FinancialDataRequirement.BASIC,
        fundamental={},
        annual=[],
        quarterly=[],
    )
    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["Fallback answer"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanySpecificFinanceHandlerV2(
        company_connector=company_connector,
        classifier=classifier,
        data_optimizer=optimizer,
    )

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    events: list[dict] = []
    async for event in handler.handle(
        ticker="ZZZZ",
        question="follow up?",
        search_decision=_decision(False),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=[
            {"role": "user", "content": "About ZZZZ."},
            {"role": "assistant", "content": "We have no data."},
        ],
    ):
        events.append(event)

    classifier.classify_data_and_period_requirement.assert_called_once()
    optimizer.fetch_optimized_data.assert_called_once()
    thinking_bodies = [e["body"] for e in events if e["type"] == "thinking_status"]
    assert any("No ZZZZ financials" in b or "conversation" in b.lower() for b in thinking_bodies)
    assert all(e["type"] != "sources" for e in events)


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.retrieve_for_analyze")
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_search_on_stuffs_brave_after_db_context(mock_multi_agent_cls, mock_retrieve):
    from services.question_analyzer.handlers_v2 import CompanySpecificFinanceHandlerV2

    company_connector, classifier, optimizer = _make_handler_deps(
        data_requirement=FinancialDataRequirement.BASIC,
        fundamental={"Name": "Apple Inc.", "MarketCap": "3T"},
    )
    mock_retrieve.return_value = AnalyzeRetrievalResult(
        sources=[
            AnalyzeSource(
                id="s_1",
                url="https://www.reuters.com/a",
                title="REUTERS_TITLE_MARKER",
                publisher="Reuters",
                published_at=None,
                is_trusted=True,
                raw_content="FULL_RAW_CONTENT_SHOULD_NOT_APPEAR",
            ),
        ],
        selected_passages=[
            AnalyzePassage(
                source_id="s_1",
                url="https://www.reuters.com/a",
                title="REUTERS_TITLE_MARKER",
                publisher="Reuters",
                published_at=None,
                is_trusted=True,
                passage_index=3,
                content="PASSAGE_MARKER gross margin reached 45%.",
            )
        ],
        query="q",
        market="GLOBAL",
        request_id="req-x",
    )
    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["A"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanySpecificFinanceHandlerV2(
        company_connector=company_connector,
        classifier=classifier,
        data_optimizer=optimizer,
    )

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    async for _event in handler.handle(
        ticker="AAPL",
        question="q",
        search_decision=_decision(True),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="req-x",
    ):
        pass

    call_kwargs = mock_agent.generate_content.call_args.kwargs
    prompt_text = call_kwargs.get("prompt", "")
    assert "REUTERS_TITLE_MARKER" in prompt_text
    assert "Sources:" in prompt_text
    assert "PASSAGE_MARKER gross margin reached 45%." in prompt_text
    assert "Passage [3]:" in prompt_text
    assert "FULL_RAW_CONTENT_SHOULD_NOT_APPEAR" not in prompt_text


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.retrieve_for_analyze")
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_search_on_uses_brave_directive_and_drops_sources_json_instructions(mock_multi_agent_cls, mock_retrieve):
    from services.question_analyzer.context_builders.components import PromptComponents
    from services.question_analyzer.handlers_v2 import CompanySpecificFinanceHandlerV2

    company_connector, classifier, optimizer = _make_handler_deps()
    mock_retrieve.return_value = AnalyzeRetrievalResult(
        sources=[
            AnalyzeSource(
                id="s_1",
                url="https://www.reuters.com/a",
                title="A",
                publisher="Reuters",
                published_at=None,
                is_trusted=True,
                raw_content="Body",
            )
        ],
        query="q",
        market="GLOBAL",
        request_id="req-d1",
    )

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["A [1]"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanySpecificFinanceHandlerV2(
        company_connector=company_connector,
        classifier=classifier,
        data_optimizer=optimizer,
    )

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    async for _event in handler.handle(
        ticker="AAPL",
        question="q",
        search_decision=_decision(True),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="req-d1",
    ):
        pass

    prompt_text = mock_agent.generate_content.call_args.kwargs.get("prompt", "")
    assert PromptComponents.grounding_rules() in prompt_text
    assert '[SOURCES_JSON]{"sources"' not in prompt_text
    assert "ALL citations must appear exclusively inside [SOURCES_JSON]" not in prompt_text


@pytest.mark.asyncio
@patch("services.question_analyzer.handlers_v2.MultiAgent")
async def test_search_off_drops_legacy_sources_json_instructions(mock_multi_agent_cls):
    from services.question_analyzer.context_builders.components import PromptComponents
    from services.question_analyzer.handlers_v2 import CompanySpecificFinanceHandlerV2

    company_connector, classifier, optimizer = _make_handler_deps()

    mock_agent = MagicMock()
    mock_agent.model_name = "test-model"
    mock_agent.generate_content.return_value = iter(["A"])
    mock_multi_agent_cls.return_value = mock_agent

    handler = CompanySpecificFinanceHandlerV2(
        company_connector=company_connector,
        classifier=classifier,
        data_optimizer=optimizer,
    )

    async def _fake_related(*_a, **_k):
        if False:
            yield {}

    handler._generate_related_questions = _fake_related  # type: ignore[attr-defined]

    async for _event in handler.handle(
        ticker="AAPL",
        question="q",
        search_decision=_decision(False),
        use_url_context=False,
        preferred_model=ModelName.Auto,
        conversation_messages=None,
        request_id="req-d2",
    ):
        pass

    prompt_text = mock_agent.generate_content.call_args.kwargs.get("prompt", "")
    assert '[SOURCES_JSON]{"sources"' not in prompt_text
    assert "ALL citations must appear exclusively inside [SOURCES_JSON]" not in prompt_text
    assert PromptComponents.grounding_rules() in prompt_text
