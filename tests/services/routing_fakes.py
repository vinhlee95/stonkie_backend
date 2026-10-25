"""Fakes for FinancialAnalyzerV2 routing (QuestionRouter + StockTickerExtractor). No LLM or DB calls."""

from __future__ import annotations

from typing import Any

from services.question_analyzer.router import RoutingResult
from services.question_analyzer.types import FinancialDataRequirement, QuestionType
from services.search_decision_engine import SearchDecision


class FakeRouter:
    """Returns a fixed RoutingResult; None makes the analyzer fall back to the legacy calls."""

    def __init__(self, result: RoutingResult | None = None):
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def route(self, **kwargs) -> RoutingResult | None:
        self.calls.append(kwargs)
        return self.result


class FakeTickerExtractor:
    def __init__(self, tickers: list[str] | None = None):
        self.tickers = tickers or []
        self.calls: list[tuple[str, str | None]] = []

    async def extract_tickers(self, question: str, current_ticker: str | None = None) -> list[str]:
        self.calls.append((question, current_ticker))
        return list(self.tickers)


def legacy_routing() -> dict[str, Any]:
    """Analyzer kwargs that force the legacy classifier + search-decision path (router fails)."""
    return {"router": FakeRouter(None), "ticker_extractor": FakeTickerExtractor()}


def make_routing(
    question_type: str = QuestionType.COMPANY_GENERAL.value,
    *,
    use_search: bool = False,
    reason_code: str | None = None,
    data_requirement: FinancialDataRequirement = FinancialDataRequirement.NONE,
    search_queries: list[str] | None = None,
    standalone_question: str = "q",
    **overrides: Any,
) -> RoutingResult:
    """A RoutingResult as the router would return it; search on gets [standalone_question] as its query."""
    values: dict[str, Any] = {
        "question_type": question_type,
        "comparison_tickers": [],
        "data_requirement": data_requirement,
        "period_requirement": None,
        "relevant_statements": None,
        "search_decision": SearchDecision(
            use_google_search=use_search,
            reason_code=reason_code or ("latest_info" if use_search else "db_data_sufficient"),
            confidence=0.9,
            decision_model="router:test",
            decision_fallback="none",
        ),
        "search_queries": search_queries
        if search_queries is not None
        else ([standalone_question] if use_search else []),
        "standalone_question": standalone_question,
    }
    values.update(overrides)
    return RoutingResult(**values)
