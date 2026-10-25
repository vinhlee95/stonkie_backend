"""Fakes for FinancialAnalyzerV2 routing (QuestionRouter + StockTickerExtractor). No LLM or DB calls."""

from __future__ import annotations

from typing import Any

from services.question_analyzer.router import RoutingResult


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
