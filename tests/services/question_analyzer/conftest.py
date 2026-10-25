from __future__ import annotations

import pytest

from services.analyze_retrieval.query_reformulator import ReformulationResult


class FakeQueryReformulator:
    """Echoes the question as the only query; the real one calls an LLM."""

    def reformulate(self, question: str, *, ticker: str | None, company_name: str) -> ReformulationResult:
        _ = (ticker, company_name)
        return ReformulationResult(queries=[question], reasoning="fake", used_fallback=False)


@pytest.fixture(autouse=True)
def _fake_query_reformulator(monkeypatch):
    """The v2 handlers' legacy (no router) search reformulates up front, before Brave."""
    monkeypatch.setattr("services.question_analyzer.handlers_v2.QueryReformulator", FakeQueryReformulator)
