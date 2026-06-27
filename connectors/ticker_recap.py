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
