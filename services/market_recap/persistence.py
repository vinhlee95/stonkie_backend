from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from models.market_recap import MarketRecap
from services.market_recap.schemas import RecapPayload


@dataclass(frozen=True)
class PersistenceResult:
    inserted: bool
    replaced: bool
    recap_id: int | None


def _payload_values(
    *,
    market: str,
    cadence: str,
    payload: RecapPayload,
    model: str,
    raw_sources: dict,
) -> dict:
    return {
        "market": market,
        "cadence": cadence,
        "period_start": payload.period_start,
        "period_end": payload.period_end,
        "summary": payload.summary,
        "bullets": [bullet.model_dump(mode="json") for bullet in payload.bullets],
        "sources": [source.model_dump(mode="json") for source in payload.sources],
        "raw_sources": raw_sources,
        "model": model,
    }


def persist_recap(
    db: Session,
    *,
    market: str,
    cadence: str,
    payload: RecapPayload,
    model: str,
    raw_sources: dict,
    replace: bool = False,
) -> PersistenceResult:
    values = _payload_values(
        market=market,
        cadence=cadence,
        payload=payload,
        model=model,
        raw_sources=raw_sources,
    )

    replaced = False
    if replace:
        db.execute(
            delete(MarketRecap).where(
                MarketRecap.market == market,
                MarketRecap.cadence == cadence,
                MarketRecap.period_start == payload.period_start,
            )
        )
        replaced = True

    statement = (
        insert(MarketRecap)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=["market", "cadence", "period_start"],
        )
        .returning(MarketRecap.id)
    )
    inserted_id = db.execute(statement).scalar_one_or_none()
    db.commit()

    if inserted_id is None:
        existing_id = db.execute(
            select(MarketRecap.id).where(
                MarketRecap.market == market,
                MarketRecap.cadence == cadence,
                MarketRecap.period_start == payload.period_start,
            )
        ).scalar_one()
        return PersistenceResult(inserted=False, replaced=False, recap_id=existing_id)

    return PersistenceResult(inserted=True, replaced=replaced, recap_id=inserted_id)
