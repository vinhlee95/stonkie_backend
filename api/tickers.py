"""HTTP route for Yahoo ticker search (public)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from connectors.yfinance_client import YFinanceClient
from services.ticker_search import TickerSearchError, search_tickers

logger = logging.getLogger(__name__)

router = APIRouter()


def get_yfinance_client() -> YFinanceClient:
    return YFinanceClient()


@router.get("/api/tickers/search")
def get_tickers_search(
    q: str = Query(..., max_length=64),
    yf_client: YFinanceClient = Depends(get_yfinance_client),
):
    if not q.strip():
        raise HTTPException(status_code=422, detail="q must not be blank")
    try:
        return {"data": search_tickers(q, yf_client)}
    except TickerSearchError:
        logger.warning("Ticker search failed for %r", q, exc_info=True)
        raise HTTPException(status_code=502, detail="Ticker search is temporarily unavailable")
