from __future__ import annotations

from typing import Any, AsyncGenerator
from unittest.mock import MagicMock, patch

import pytest

from services.financial_analyzer_v2 import FinancialAnalyzerV2
from services.question_analyzer.router import RoutingResult
from services.question_analyzer.types import QuestionType
from services.search_decision_engine import SearchDecision
from tests.services.routing_fakes import FakeRouter, FakeTickerExtractor, make_routing


def _stub_financial_connector() -> MagicMock:
    fc = MagicMock()
    fc.get_available_periods.return_value = None
    fc.get_available_metrics.return_value = None
    return fc


class _CapturingHandler:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def handle(
        self,
        ticker: str,
        question: str,
        search_decision: SearchDecision,
        use_url_context: bool,
        preferred_model,
        conversation_messages,
        request_id: str,
        debug_prompt_context: bool = False,
        *,
        routing: RoutingResult,
    ) -> AsyncGenerator[dict[str, Any], None]:
        _ = debug_prompt_context
        self.calls.append(
            {
                "ticker": ticker,
                "question": question,
                "search_decision": search_decision,
                "use_url_context": use_url_context,
                "request_id": request_id,
                "routing": routing,
            }
        )
        yield {"type": "thinking_status", "body": "Analyzing...", "phase": "analyze", "step": 3}
        yield {"type": "answer", "body": "hello"}
        yield {"type": "model_used", "body": "test-model"}


def _analyzer(router: FakeRouter, *, financial_connector=None, extractor=None, **kwargs) -> FinancialAnalyzerV2:
    return FinancialAnalyzerV2(
        router=router,
        ticker_extractor=extractor or FakeTickerExtractor(),
        company_financial_connector=financial_connector or _stub_financial_connector(),
        **kwargs,
    )


def _handlers(company_general) -> dict[str, Any]:
    return {
        "company_general_handler": company_general,
        "general_finance_handler": MagicMock(),
        "company_specific_finance_handler": MagicMock(),
        "comparison_handler": MagicMock(),
    }


@pytest.mark.asyncio
async def test_analyzer_v2_dispatches_company_general_handler_and_forwards_search_decision():
    routing = make_routing(use_search=False, reason_code="stable_concept", standalone_question="What does Apple do?")
    handler = _CapturingHandler()
    analyzer = _analyzer(FakeRouter(routing), **_handlers(handler))

    events: list[dict[str, Any]] = []
    async for event in analyzer.analyze_question(ticker="AAPL", question="What does Apple do?"):
        events.append(event)

    assert handler.calls
    assert handler.calls[0]["search_decision"] == routing.search_decision
    assert handler.calls[0]["routing"] == routing
    assert handler.calls[0]["ticker"] == "AAPL"
    assert [event["type"] for event in events] == [
        "thinking_status",
        "thinking_status",
        "answer",
        "model_used",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("use_search", "expected_body"),
    [
        (True, "Searching for the latest AAPL data..."),
        (False, "Found AAPL data in our database"),
    ],
)
async def test_analyzer_v2_emits_search_thinking_status_after_routing(use_search, expected_body):
    handler = _CapturingHandler()
    analyzer = _analyzer(FakeRouter(make_routing(use_search=use_search)), **_handlers(handler))

    events: list[dict[str, Any]] = []
    async for event in analyzer.analyze_question(ticker="AAPL", question="What does Apple do?"):
        events.append(event)

    assert events[0]["type"] == "thinking_status"
    assert events[0]["body"] == expected_body


@pytest.mark.asyncio
async def test_router_receives_available_periods_and_metrics_for_valid_ticker():
    periods = {"annual": ["2023"]}
    metrics = ["Revenue", "EBITDA"]
    financial_connector = MagicMock()
    financial_connector.get_available_periods.return_value = periods
    financial_connector.get_available_metrics.return_value = metrics
    router = FakeRouter(make_routing())
    analyzer = _analyzer(router, financial_connector=financial_connector, **_handlers(_CapturingHandler()))

    async for _ in analyzer.analyze_question(ticker="AAPL", question="What is Apple?"):
        pass

    (call,) = router.calls
    assert call["available_periods"] == periods
    assert call["available_metrics"] == metrics
    financial_connector.get_available_periods.assert_called_once_with("AAPL")
    financial_connector.get_available_metrics.assert_called_once_with("AAPL")


@pytest.mark.asyncio
async def test_no_db_hint_fetch_when_ticker_is_none_placeholder():
    financial_connector = MagicMock()
    router = FakeRouter(make_routing())
    analyzer = _analyzer(router, financial_connector=financial_connector, **_handlers(_CapturingHandler()))

    async for _ in analyzer.analyze_question(ticker="none", question="Macro question"):
        pass

    financial_connector.get_available_periods.assert_not_called()
    financial_connector.get_available_metrics.assert_not_called()
    (call,) = router.calls
    assert call["available_periods"] is None
    assert call["available_metrics"] is None


@pytest.mark.asyncio
async def test_sec_url_in_question_forces_search_reason_and_emits_attachment_before_routing_status():
    handler = _CapturingHandler()
    analyzer = _analyzer(FakeRouter(make_routing(use_search=False)), **_handlers(handler))

    sec_url = "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm"
    question = f"Summarize this filing {sec_url}"
    events: list[dict[str, Any]] = []
    async for e in analyzer.analyze_question(ticker="AAPL", question=question):
        events.append(e)

    assert events[0]["type"] == "attachment_url"
    assert events[0]["body"] == sec_url
    decision = handler.calls[0]["search_decision"]
    assert decision.use_google_search is True
    assert decision.reason_code == "sec_url"
    assert any(e["type"] == "thinking_status" and "Searching for the latest" in str(e.get("body", "")) for e in events)


@pytest.mark.asyncio
@patch(
    "services.financial_analyzer_v2.validate_pdf_url",
    return_value=(False, "not a real pdf"),
)
async def test_invalid_non_sec_pdf_url_returns_error_without_routing(_mock_validate):
    router = FakeRouter(make_routing())
    analyzer = _analyzer(router)

    question = "Review https://example.com/doc.pdf"
    events: list[dict[str, Any]] = []
    async for e in analyzer.analyze_question(ticker="AAPL", question=question):
        events.append(e)

    assert router.calls == []
    assert any(e["type"] == "answer" and "not a real pdf" in e["body"] for e in events)
    assert not any(e["type"] == "search_decision_meta" for e in events)


@pytest.mark.asyncio
@patch("services.financial_analyzer_v2.validate_pdf_url", return_value=(False, "not a real pdf"))
async def test_use_url_context_allows_non_pdf_url_to_reach_handler(_mock_validate):
    handler = _CapturingHandler()
    analyzer = _analyzer(FakeRouter(make_routing(use_search=True)), **_handlers(handler))

    events: list[dict[str, Any]] = []
    async for event in analyzer.analyze_question(
        ticker="AAPL",
        question="Analyze https://example.com/report",
        use_url_context=True,
    ):
        events.append(event)

    _mock_validate.assert_not_called()
    assert events[0] == {"type": "attachment_url", "body": "https://example.com/report"}
    assert handler.calls[0]["use_url_context"] is True
    assert any(e["type"] == "answer" and e["body"] == "hello" for e in events)


@pytest.mark.asyncio
@patch("services.financial_analyzer_v2.MultiAgent")
async def test_valid_non_sec_pdf_short_circuits_through_pdf_handler(mock_ma_cls):
    mock_agent = MagicMock()
    mock_agent.model_name = "m1"
    mock_agent.generate_content_with_pdf_url.return_value = iter(["chunk-a"])
    mock_ma_cls.return_value = mock_agent

    company_connector = MagicMock()
    company_connector.get_fundamental_data.return_value = None
    router = FakeRouter(make_routing())
    analyzer = _analyzer(router, company_connector=company_connector)

    with patch("services.financial_analyzer_v2.validate_pdf_url", return_value=(True, "")):
        events: list[dict[str, Any]] = []
        async for e in analyzer.analyze_question(
            ticker="AAPL",
            question="Read https://example.com/fake.pdf",
        ):
            events.append(e)

    assert router.calls == []
    assert any(e["type"] == "answer" and e["body"] == "chunk-a" for e in events)
    assert any(e["type"] == "model_used" for e in events)


@pytest.mark.asyncio
async def test_extractor_gets_raw_ticker_and_router_gets_normalized_ticker():
    router = FakeRouter(make_routing(QuestionType.COMPANY_GENERAL.value))
    extractor = FakeTickerExtractor()
    analyzer = _analyzer(router, extractor=extractor, **_handlers(_CapturingHandler()))

    raw_ticker = "  msft  "
    async for _ in analyzer.analyze_question(ticker=raw_ticker, question="Overview?"):
        pass

    assert extractor.calls == [("Overview?", raw_ticker)]
    assert router.calls[0]["ticker"] == "MSFT"
