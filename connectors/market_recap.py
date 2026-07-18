from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select

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
    audio_key: str | None = None
    audio_duration_s: float | None = None


def _to_dto(row: MarketRecap) -> MarketRecapDto:
    return MarketRecapDto(
        id=row.id,
        market=row.market,
        cadence=row.cadence,
        period_start=row.period_start,
        period_end=row.period_end,
        summary=row.summary,
        bullets=list(row.bullets or []),
        sources=list(row.sources or []),
        audio_key=row.audio_key,
        audio_duration_s=row.audio_duration_s,
    )


class MarketRecapConnector:
    def get_by_id(self, recap_id: int) -> MarketRecapDto | None:
        with SessionLocal() as db:
            row = db.query(MarketRecap).filter(MarketRecap.id == recap_id).one_or_none()
            if row is None:
                return None
            return _to_dto(row)

    def set_audio(self, recap_id: int, *, audio_key: str, audio_duration_s: float) -> bool:
        with SessionLocal() as db:
            row = db.get(MarketRecap, recap_id)
            if row is None:
                return False
            row.audio_key = audio_key
            row.audio_duration_s = audio_duration_s
            db.commit()
            return True

    def get_without_audio(self, *, cadence: str, limit: int = 50, since: date | None = None) -> list[MarketRecapDto]:
        """Recaps still missing audio. `since` bounds how far back to look --
        without it the query walks the whole archive once recent rows are done."""
        conditions = [MarketRecap.cadence == cadence, MarketRecap.audio_key.is_(None)]
        if since is not None:
            conditions.append(MarketRecap.period_start >= since)
        with SessionLocal() as db:
            rows = (
                db.execute(
                    select(MarketRecap).where(*conditions).order_by(MarketRecap.period_start.desc()).limit(limit)
                )
                .scalars()
                .all()
            )
            return [_to_dto(row) for row in rows]
