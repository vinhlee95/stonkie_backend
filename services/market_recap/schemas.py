from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator


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
