from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, computed_field, model_validator

from services.market_recap.url_utils import canonicalize_url, source_id_for


class Source(BaseModel):
    id: str
    url: str
    title: str
    publisher: str
    published_at: datetime
    fetched_at: datetime


class Citation(BaseModel):
    source_id: str


class Bullet(BaseModel):
    text: str
    citations: list[Citation] = Field(min_length=1)


class RecapPayload(BaseModel):
    period_start: date
    period_end: date
    summary: str
    bullets: list[Bullet]
    sources: list[Source]

    @model_validator(mode="after")
    def validate_citations_reference_sources(self) -> "RecapPayload":
        source_ids = {source.id for source in self.sources}
        unknown_ids = {
            citation.source_id
            for bullet in self.bullets
            for citation in bullet.citations
            if citation.source_id not in source_ids
        }
        if unknown_ids:
            raise ValueError(f"unknown source_id citations: {', '.join(sorted(unknown_ids))}")
        return self


class PlannedQuery(BaseModel):
    query: str
    include_domains: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    title: str
    url: str
    snippet: str = ""
    published_date: datetime | None = None
    raw_content: str = ""
    score: float = 0.0
    provider: str

    @computed_field
    @property
    def canonical_url(self) -> str:
        return canonicalize_url(self.url)

    @computed_field
    @property
    def source_id(self) -> str:
        return source_id_for(self.url)


class RetrievalStats(BaseModel):
    queries_total: int
    results_total: int
    deduped: int
    with_raw_content: int
    allowlisted: int
    ranked_top_k: int


class RetrievalResult(BaseModel):
    candidates: list[Candidate]
    stats: RetrievalStats
    query_snapshots: list[dict[str, Any]] = Field(default_factory=list)
