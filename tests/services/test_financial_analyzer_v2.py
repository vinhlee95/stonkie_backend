from __future__ import annotations

from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.question_analyzer.types import QuestionType
from services.search_decision_engine import SearchDecision


def _stub_financial_connector() -> MagicMock:
    fc = MagicMock()
    fc.get_available_periods.return_value = None
    fc.get_available_metrics.return_value = None
    return fc


class _FakeClassifier:
    async def classify_question_type(self, question: str, ticker: str, conversation_messages=None):
        _ = (question, ticker, conversation_messages)
        return "company-general", None


class _FakeSearchDecisionEngine:
    def __init__(self, decision: SearchDecision):
        self._decision = decision

    async def decide(self, **_kwargs) -> SearchDecision:
        return self._decision


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
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.calls.append(
            {
                "ticker": ticker,
                "question": question,
                "search_decision": search_decision,
                "use_url_context": use_url_context,
                "request_id": request_id,
            }
        )
        yield {"type": "thinking_status", "body": "Analyzing...", "phase": "analyze", "step": 3}
        yield {"type": "answer", "body": "hello"}
        yield {"type": "model_used", "body": "test-model"}


@pytest.mark.asyncio
async def test_analyzer_v2_dispatches_company_general_handler_and_forwards_search_decision():
    from services.financial_analyzer_v2 import FinancialAnalyzerV2

    decision = SearchDecision(
        use_google_search=False,
        reason_code="stable_concept",
        confidence=0.9,
        decision_model="test",
        decision_fallback="none",
    )
    handler = _CapturingHandler()
    analyzer = FinancialAnalyzerV2(
        classifier=_FakeClassifier(),
        search_decision_engine=_FakeSearchDecisionEngine(decision),
        company_general_handler=handler,
        company_financial_connector=_stub_financial_connector(),
    )

    events: list[dict[str, Any]] = []
    async for event in analyzer.analyze_question(
        ticker="AAPL",
        question="What does Apple do?",
    ):
        events.append(event)

    assert handler.calls
    assert handler.calls[0]["search_decision"] == decision
    assert handler.calls[0]["ticker"] == "AAPL"
    assert [event["type"] for event in events] == [
        "search_decision_meta",
        "thinking_status",
        "answer",
        "model_used",
    ]


@pytest.mark.asyncio
async def test_analyzer_v2_emits_classifier_error_when_unclassified():
    from services.financial_analyzer_v2 import FinancialAnalyzerV2

    class _NoneClassifier:
        async def classify_question_type(self, question: str, ticker: str, conversation_messages=None):
            _ = (question, ticker, conversation_messages)
            return None, None

    decision = SearchDecision(
        use_google_search=False,
        reason_code="stable_concept",
        confidence=0.9,
        decision_model="test",
        decision_fallback="none",
    )
    analyzer = FinancialAnalyzerV2(
        classifier=_NoneClassifier(),
        search_decision_engine=_FakeSearchDecisionEngine(decision),
        company_financial_connector=_stub_financial_connector(),
    )

    events: list[dict[str, Any]] = []
    async for event in analyzer.analyze_question(
        ticker="AAPL",
        question="?",
    ):
        events.append(event)

    assert events[-1]["type"] == "answer"
    assert "Unable to classify question type" in events[-1]["body"]


@pytest.mark.asyncio
async def test_decide_receives_available_periods_and_metrics_for_valid_ticker():
    """SearchDecisionEngine.decide must get DB hints like v1 (parity with FinancialAnalyzer)."""
    from services.financial_analyzer_v2 import FinancialAnalyzerV2

    periods = {"annual": ["2023"]}
    metrics = ["Revenue", "EBITDA"]
    financial_connector = MagicMock()
    financial_connector.get_available_periods.return_value = periods
    financial_connector.get_available_metrics.return_value = metrics

    captured: dict[str, Any] = {}

    async def decide_side_effect(**kwargs: Any) -> SearchDecision:
        captured.clear()
        captured.update(kwargs)
        return SearchDecision(
            use_google_search=False,
            reason_code="stable_concept",
            confidence=0.9,
            decision_model="m",
            decision_fallback="none",
        )

    sd = MagicMock()
    sd.decide = AsyncMock(side_effect=decide_side_effect)

    class _Clf:
        async def classify_question_type(self, question: str, ticker: str, conversation_messages=None):
            _ = (question, ticker, conversation_messages)
            return QuestionType.COMPANY_GENERAL.value, None

    handler = MagicMock()

    async def _gen():
        yield {"type": "answer", "body": "x"}

    handler.handle = MagicMock(return_value=_gen())

    analyzer = FinancialAnalyzerV2(
        classifier=_Clf(),
        search_decision_engine=sd,
        company_general_handler=handler,
        company_financial_connector=financial_connector,
    )

    async for _ in analyzer.analyze_question(ticker="AAPL", question="What is Apple?"):
        pass

    assert captured.get("available_periods") == periods
    assert captured.get("available_metrics") == metrics
    assert captured.get("force_google_search_reason") is None
    financial_connector.get_available_periods.assert_called_once_with("AAPL")
    financial_connector.get_available_metrics.assert_called_once_with("AAPL")


@pytest.mark.asyncio
async def test_no_db_hint_fetch_when_ticker_is_none_placeholder():
    from services.financial_analyzer_v2 import FinancialAnalyzerV2

    financial_connector = MagicMock()
    captured: dict[str, Any] = {}

    async def decide_side_effect(**kwargs: Any) -> SearchDecision:
        captured.update(kwargs)
        return SearchDecision(
            use_google_search=False,
            reason_code="stable_concept",
            confidence=0.9,
            decision_model="m",
            decision_fallback="none",
        )

    sd = MagicMock()
    sd.decide = AsyncMock(side_effect=decide_side_effect)

    class _Clf:
        async def classify_question_type(self, question: str, ticker: str, conversation_messages=None):
            return QuestionType.COMPANY_GENERAL.value, None

    handler = MagicMock()

    async def _gen():
        yield {"type": "answer", "body": "x"}

    handler.handle = MagicMock(return_value=_gen())

    analyzer = FinancialAnalyzerV2(
        classifier=_Clf(),
        search_decision_engine=sd,
        company_general_handler=handler,
        company_financial_connector=financial_connector,
    )

    async for _ in analyzer.analyze_question(ticker="none", question="Macro question"):
        pass

    financial_connector.get_available_periods.assert_not_called()
    financial_connector.get_available_metrics.assert_not_called()
    assert captured.get("available_periods") is None
    assert captured.get("available_metrics") is None


@pytest.mark.asyncio
async def test_sec_url_in_question_forces_search_reason_and_emits_attachment_before_meta():
    from services.financial_analyzer_v2 import FinancialAnalyzerV2

    financial_connector = _stub_financial_connector()
    captured: dict[str, Any] = {}

    async def decide_side_effect(**kwargs: Any) -> SearchDecision:
        captured.update(kwargs)
        return SearchDecision(
            use_google_search=True,
            reason_code="sec_url",
            confidence=1.0,
            decision_model="m",
            decision_fallback="none",
        )

    sd = MagicMock()
    sd.decide = AsyncMock(side_effect=decide_side_effect)

    class _Clf:
        async def classify_question_type(self, question: str, ticker: str, conversation_messages=None):
            return QuestionType.COMPANY_GENERAL.value, None

    handler = MagicMock()

    async def _gen():
        yield {"type": "answer", "body": "ok"}

    handler.handle = MagicMock(return_value=_gen())

    analyzer = FinancialAnalyzerV2(
        classifier=_Clf(),
        search_decision_engine=sd,
        company_general_handler=handler,
        company_financial_connector=financial_connector,
    )

    sec_url = "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm"
    question = f"Summarize this filing {sec_url}"
    events: list[dict[str, Any]] = []
    async for e in analyzer.analyze_question(ticker="AAPL", question=question):
        events.append(e)

    assert events[0]["type"] == "attachment_url"
    assert events[0]["body"] == sec_url
    assert captured.get("force_google_search_reason") == "sec_url"
    assert any(e["type"] == "search_decision_meta" for e in events)


@pytest.mark.asyncio
@patch(
    "services.financial_analyzer_v2.validate_pdf_url",
    return_value=(False, "not a real pdf"),
)
async def test_invalid_non_sec_pdf_url_returns_error_without_decide(_mock_validate):
    from services.financial_analyzer_v2 import FinancialAnalyzerV2

    sd = MagicMock()
    sd.decide = AsyncMock()
    analyzer = FinancialAnalyzerV2(
        search_decision_engine=sd,
        company_financial_connector=_stub_financial_connector(),
    )

    question = "Review https://example.com/doc.pdf"
    events: list[dict[str, Any]] = []
    async for e in analyzer.analyze_question(ticker="AAPL", question=question):
        events.append(e)

    sd.decide.assert_not_called()
    assert any(e["type"] == "answer" and "not a real pdf" in e["body"] for e in events)
    assert not any(e["type"] == "search_decision_meta" for e in events)


@pytest.mark.asyncio
@patch("services.financial_analyzer_v2.MultiAgent")
async def test_valid_non_sec_pdf_short_circuits_through_pdf_handler(mock_ma_cls):
    from services.financial_analyzer_v2 import FinancialAnalyzerV2

    sd = MagicMock()
    sd.decide = AsyncMock()
    mock_agent = MagicMock()
    mock_agent.model_name = "m1"
    mock_agent.generate_content_with_pdf_url.return_value = iter(["chunk-a"])
    mock_ma_cls.return_value = mock_agent

    company_connector = MagicMock()
    company_connector.get_fundamental_data.return_value = None

    analyzer = FinancialAnalyzerV2(
        search_decision_engine=sd,
        company_connector=company_connector,
        company_financial_connector=_stub_financial_connector(),
    )

    with patch("services.financial_analyzer_v2.validate_pdf_url", return_value=(True, "")):
        events: list[dict[str, Any]] = []
        async for e in analyzer.analyze_question(
            ticker="AAPL",
            question="Read https://example.com/fake.pdf",
        ):
            events.append(e)

    sd.decide.assert_not_called()
    assert any(e["type"] == "answer" and e["body"] == "chunk-a" for e in events)
    assert any(e["type"] == "model_used" for e in events)


@pytest.mark.asyncio
async def test_company_specific_handler_receives_available_metrics():
    from services.financial_analyzer_v2 import FinancialAnalyzerV2

    metrics_list = ["Revenue", "NetIncome"]
    financial_connector = MagicMock()
    financial_connector.get_available_periods.return_value = {}
    financial_connector.get_available_metrics.return_value = metrics_list

    sd = MagicMock()
    sd.decide = AsyncMock(
        return_value=SearchDecision(
            use_google_search=False,
            reason_code="stable_concept",
            confidence=0.9,
            decision_model="m",
            decision_fallback="none",
        )
    )

    cs = MagicMock()

    async def _gen():
        yield {"type": "answer", "body": "ok"}

    cs.handle = MagicMock(return_value=_gen())

    class _ClfSpec:
        async def classify_question_type(self, question, ticker, conversation_messages=None):
            _ = (question, ticker, conversation_messages)
            return QuestionType.COMPANY_SPECIFIC_FINANCE.value, None

    analyzer = FinancialAnalyzerV2(
        classifier=_ClfSpec(),
        search_decision_engine=sd,
        company_specific_finance_handler=cs,
        company_financial_connector=financial_connector,
    )

    async for _ in analyzer.analyze_question(ticker="MSFT", question="What was revenue?"):
        pass

    kw = cs.handle.call_args.kwargs
    assert kw.get("available_metrics") == metrics_list


@pytest.mark.asyncio
async def test_classifier_receives_raw_ticker_matching_v1():
    """v1 FinancialAnalyzer passes original `ticker` to classify_question_type, not normalized."""
    from services.financial_analyzer_v2 import FinancialAnalyzerV2

    received_ticker: dict[str, str] = {}

    class _CapturingClf:
        async def classify_question_type(self, question, ticker_arg, conversation_messages=None):
            _ = (question, conversation_messages)
            received_ticker["value"] = ticker_arg
            return QuestionType.COMPANY_GENERAL.value, None

    sd = MagicMock()
    sd.decide = AsyncMock(
        return_value=SearchDecision(
            use_google_search=False,
            reason_code="stable_concept",
            confidence=0.9,
            decision_model="m",
            decision_fallback="none",
        )
    )
    handler = MagicMock()

    async def _gen():
        yield {"type": "answer", "body": "x"}

    handler.handle = MagicMock(return_value=_gen())

    analyzer = FinancialAnalyzerV2(
        classifier=_CapturingClf(),
        search_decision_engine=sd,
        company_general_handler=handler,
        company_financial_connector=_stub_financial_connector(),
    )

    raw_ticker = "  msft  "
    async for _ in analyzer.analyze_question(ticker=raw_ticker, question="Overview?"):
        pass

    assert received_ticker["value"] == raw_ticker
    sd.decide.assert_awaited_once()
    assert sd.decide.await_args.kwargs["ticker"] == "MSFT"
