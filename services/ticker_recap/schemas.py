from datetime import date

from pydantic import BaseModel, model_validator

from services.market_recap.schemas import Bullet, Citation, Source

__all__ = ["Bullet", "Citation", "Source", "TickerRecapPayload"]


class TickerRecapPayload(BaseModel):
    ticker: str
    cadence: str
    period_start: date
    period_end: date
    summary: str
    bullets: list[Bullet]
    sources: list[Source]

    @model_validator(mode="after")
    def validate_citations_reference_sources(self) -> "TickerRecapPayload":
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
