from datetime import UTC, datetime

from fastapi import APIRouter, Query

from services.ticker_recap.reader import get_latest_recaps

router = APIRouter()


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@router.get("/api/companies/{ticker}/recaps")
def get_company_recaps(
    ticker: str,
    cadence: str = Query("daily"),
    limit: int = Query(1, ge=1, le=52),
):
    ticker = ticker.upper()
    recaps = get_latest_recaps(ticker, cadence, limit=limit)

    latest_created_at = max((recap.created_at for recap in recaps), default=None)
    items = [
        {
            "id": recap.id,
            "period_start": recap.period_start.isoformat(),
            "period_end": recap.period_end.isoformat(),
            "created_at": _isoformat(recap.created_at),
            "summary": recap.summary,
            "bullets": recap.bullets,
            "sources": recap.sources,
            "price_change": recap.price_change,
        }
        for recap in recaps
    ]

    return {
        "ticker": ticker,
        "cadence": cadence,
        "latest_created_at": _isoformat(latest_created_at),
        "items": items,
    }
