from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from connectors.database import SessionLocal
from models.ticker_recap import TickerRecap
from services.ticker_recap.schemas import TickerRecapPayload


@dataclass(frozen=True)
class TickerRecapDto:
    id: int
    ticker: str
    cadence: str
    period_start: date
    period_end: date
    summary: str
    bullets: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    price_change: dict[str, Any] | None
    search_query: str | None
    created_at: datetime | None
    audio_key: str | None = None
    audio_duration_s: float | None = None


@dataclass(frozen=True)
class UpsertResult:
    inserted: bool
    replaced: bool
    recap_id: int | None


def _to_dto(row: TickerRecap) -> TickerRecapDto:
    return TickerRecapDto(
        id=row.id,
        ticker=row.ticker,
        cadence=row.cadence,
        period_start=row.period_start,
        period_end=row.period_end,
        summary=row.summary,
        bullets=list(row.bullets or []),
        sources=list(row.sources or []),
        price_change=row.price_change,
        search_query=row.search_query,
        created_at=row.created_at,
        audio_key=row.audio_key,
        audio_duration_s=row.audio_duration_s,
    )


class TickerRecapConnector:
    """Repository for the ticker_recap table. Owns its DB sessions; callers in the
    service layer inject this connector and consume DTOs (no ORM/Session leakage)."""

    def upsert_recap(
        self,
        *,
        ticker: str,
        cadence: str,
        payload: TickerRecapPayload,
        model: str,
        raw_sources: dict | None = None,
        price_change: dict | None = None,
        search_query: str | None = None,
        replace: bool = False,
    ) -> UpsertResult:
        values = {
            "ticker": ticker,
            "cadence": cadence,
            "period_start": payload.period_start,
            "period_end": payload.period_end,
            "summary": payload.summary,
            "bullets": [bullet.model_dump(mode="json") for bullet in payload.bullets],
            "sources": [source.model_dump(mode="json") for source in payload.sources],
            "raw_sources": raw_sources,
            "price_change": price_change,
            "search_query": search_query,
            "model": model,
        }

        with SessionLocal() as db:
            replaced = False
            if replace:
                db.execute(
                    delete(TickerRecap).where(
                        TickerRecap.ticker == ticker,
                        TickerRecap.cadence == cadence,
                        TickerRecap.period_start == payload.period_start,
                    )
                )
                replaced = True

            statement = (
                insert(TickerRecap)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["ticker", "cadence", "period_start"])
                .returning(TickerRecap.id)
            )
            inserted_id = db.execute(statement).scalar_one_or_none()
            db.commit()

            if inserted_id is None:
                existing_id = db.execute(
                    select(TickerRecap.id).where(
                        TickerRecap.ticker == ticker,
                        TickerRecap.cadence == cadence,
                        TickerRecap.period_start == payload.period_start,
                    )
                ).scalar_one()
                return UpsertResult(inserted=False, replaced=False, recap_id=existing_id)

            return UpsertResult(inserted=True, replaced=replaced, recap_id=inserted_id)

    def set_audio(self, recap_id: int, *, audio_key: str, audio_duration_s: float) -> bool:
        with SessionLocal() as db:
            row = db.get(TickerRecap, recap_id)
            if row is None:
                return False
            row.audio_key = audio_key
            row.audio_duration_s = audio_duration_s
            db.commit()
            return True

    def get_without_audio(self, *, cadence: str, limit: int = 50, since: date | None = None) -> list[TickerRecapDto]:
        """Recaps still missing audio. `since` bounds how far back to look --
        without it the query walks the whole archive once recent rows are done."""
        conditions = [TickerRecap.cadence == cadence, TickerRecap.audio_key.is_(None)]
        if since is not None:
            conditions.append(TickerRecap.period_start >= since)
        with SessionLocal() as db:
            rows = (
                db.execute(
                    select(TickerRecap).where(*conditions).order_by(TickerRecap.period_start.desc()).limit(limit)
                )
                .scalars()
                .all()
            )
            return [_to_dto(row) for row in rows]

    def get_by_id(self, recap_id: int) -> TickerRecapDto | None:
        with SessionLocal() as db:
            row = db.get(TickerRecap, recap_id)
            return _to_dto(row) if row is not None else None

    def get_latest(self, ticker: str, cadence: str, *, limit: int = 1) -> list[TickerRecapDto]:
        with SessionLocal() as db:
            rows = (
                db.execute(
                    select(TickerRecap)
                    .where(TickerRecap.ticker == ticker, TickerRecap.cadence == cadence)
                    .order_by(TickerRecap.period_start.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [_to_dto(row) for row in rows]
