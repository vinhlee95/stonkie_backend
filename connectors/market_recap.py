from dataclasses import dataclass
from datetime import date
from typing import Any

from connectors.database import SessionLocal
from models.market_recap import MarketRecap


@dataclass(frozen=True)
class MarketRecapDto:
    id: int
    market: str
    cadence: str
    period_start: date
    period_end: date
    summary: str
    bullets: list[dict[str, Any]]
    sources: list[dict[str, Any]]


class MarketRecapConnector:
    def get_by_id(self, recap_id: int) -> MarketRecapDto | None:
        with SessionLocal() as db:
            row = db.query(MarketRecap).filter(MarketRecap.id == recap_id).one_or_none()
            if row is None:
                return None
            return MarketRecapDto(
                id=row.id,
                market=row.market,
                cadence=row.cadence,
                period_start=row.period_start,
                period_end=row.period_end,
                summary=row.summary,
                bullets=list(row.bullets or []),
                sources=list(row.sources or []),
            )
