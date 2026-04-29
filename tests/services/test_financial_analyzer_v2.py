from __future__ import annotations

from typing import Any, AsyncGenerator

import pytest

from services.search_decision_engine import SearchDecision


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
    )

    events: list[dict[str, Any]] = []
    async for event in analyzer.analyze_question(
        ticker="AAPL",
        question="?",
    ):
        events.append(event)

    assert events[-1]["type"] == "answer"
    assert "Unable to classify question type" in events[-1]["body"]
