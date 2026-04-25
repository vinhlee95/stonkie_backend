from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from connectors.database import get_db
from models.market_recap import MarketRecap

router = APIRouter()


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@router.get("/api/markets/{market}/recaps")
def get_market_recaps(
    market: str,
    cadence: str = Query("weekly"),
    limit: int = Query(1, ge=1, le=52),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(MarketRecap)
        .filter(MarketRecap.market == market.upper(), MarketRecap.cadence == cadence)
        .order_by(MarketRecap.period_start.desc())
        .limit(limit)
        .all()
    )

    latest_created_at = max((row.created_at for row in rows), default=None)
    items = [
        {
            "period_start": row.period_start.isoformat(),
            "period_end": row.period_end.isoformat(),
            "created_at": _isoformat(row.created_at),
            "summary": row.summary,
            "bullets": row.bullets,
            "sources": row.sources,
        }
        for row in rows
    ]

    return {
        "market": market.upper(),
        "cadence": cadence,
        "latest_created_at": _isoformat(latest_created_at),
        "items": items,
    }
