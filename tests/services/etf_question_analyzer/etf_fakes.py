"""Shared ETF test data and fakes for the v2 ETF tests. No LLM, DB or Brave calls."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from connectors.etf_fundamental import (
    ETFCountryAllocationDto,
    ETFFundamentalDto,
    ETFHoldingDto,
    ETFSectorAllocationDto,
)
from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeRetrievalResult, AnalyzeSource


def make_etf(ticker: str = "SXR8", **overrides: Any) -> ETFFundamentalDto:
    etf = ETFFundamentalDto(
        isin="IE00B5BMR087",
        ticker=ticker,
        name="iShares Core S&P 500 UCITS ETF",
        fund_provider="iShares",
        fund_size_billions=114.6,
        ter_percent=0.07,
        replication_method="Physical",
        distribution_policy="Accumulating",
        fund_currency="USD",
        domicile="Ireland",
        launch_date="2010-05-19",
        index_tracked="S&P 500",
        holdings=[ETFHoldingDto(name="NVIDIA", weight_percent=7.38), ETFHoldingDto(name="Apple", weight_percent=6.9)],
        sector_allocation=[ETFSectorAllocationDto(sector="Technology", weight_percent=33.1)],
        country_allocation=[ETFCountryAllocationDto(country="United States", weight_percent=99.5)],
    )
    return replace(etf, **overrides)


def make_source(source_id: str = "s_1", url: str = "https://www.justetf.com/en/etf-profile.html") -> AnalyzeSource:
    return AnalyzeSource(
        id=source_id,
        url=url,
        title="SXR8 profile",
        publisher="justETF",
        published_at=datetime(2026, 5, 1, tzinfo=UTC),
        is_trusted=True,
        raw_content="SXR8 has a TER of 0.07%.",
    )


def make_retrieval(*sources: AnalyzeSource) -> AnalyzeRetrievalResult:
    sources = sources or (make_source(),)
    passages = [
        AnalyzePassage(
            source_id=s.id,
            url=s.url,
            title=s.title,
            publisher=s.publisher,
            published_at=s.published_at,
            is_trusted=s.is_trusted,
            passage_index=0,
            content=f"Passage from {s.id}: TER is 0.07%.",
        )
        for s in sources
    ]
    return AnalyzeRetrievalResult(
        sources=list(sources),
        query="q",
        market="GLOBAL",
        request_id="req",
        selected_passages=passages,
    )


class FakeAgent:
    """Stands in for MultiAgent: records the prompt, streams fixed chunks (incl. a citation dict)."""

    def __init__(self, chunks: list[Any] | None = None, model_name: str = "test-model"):
        self.chunks = chunks if chunks is not None else ["Alpha ", {"type": "url_citation"}, "Beta"]
        self.model_name = model_name
        self.prompts: list[str] = []
        self.search_flags: list[bool] = []

    def generate_content(self, prompt: str, use_google_search: bool = False, **_kwargs):
        self.prompts.append(prompt)
        self.search_flags.append(use_google_search)
        return iter(self.chunks)


class FakeAgentFactory:
    def __init__(self, agent: FakeAgent | None = None):
        self.agent = agent or FakeAgent()
        self.models: list[Any] = []

    def __call__(self, model_name: Any = None) -> FakeAgent:
        self.models.append(model_name)
        return self.agent


class FakeRetriever:
    """Replaces retrieve_for_analyze; records kwargs. `results` maps company_name -> result or exception."""

    def __init__(self, result: Any = None, by_company: dict[str, Any] | None = None):
        self.result = result if result is not None else make_retrieval()
        self.by_company = by_company or {}
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> AnalyzeRetrievalResult:
        self.calls.append(kwargs)
        outcome = self.by_company.get(kwargs.get("company_name"), self.result)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def no_related_questions(*_args: Any, **_kwargs: Any):
    if False:  # pragma: no cover
        yield {}


async def two_related_questions(*_args: Any, **_kwargs: Any):
    yield {"type": "related_question", "body": "RQ1"}
    yield {"type": "related_question", "body": "RQ2"}
