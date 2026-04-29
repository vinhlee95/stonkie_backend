from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from services.analyze_retrieval.source_policy import Market


class AnalyzeSource(BaseModel):
    id: str
    url: str
    title: str
    publisher: str
    published_at: datetime | None = None
    is_trusted: bool


class AnalyzeRetrievalResult(BaseModel):
    sources: list[AnalyzeSource]
    query: str
    market: Market
    request_id: str


class BraveRetrievalError(Exception):
    pass
