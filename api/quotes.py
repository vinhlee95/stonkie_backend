"""HTTP route for batch daily price changes."""

from fastapi import APIRouter, Depends, HTTPException, Query

from connectors.yfinance_client import YFinanceClient
from services.price_change import get_price_changes

router = APIRouter()


def get_yfinance_client() -> YFinanceClient:
    return YFinanceClient()


@router.get("/api/quotes/price-changes")
def get_quotes_price_changes(
    tickers: str = Query(...),
    yf_client: YFinanceClient = Depends(get_yfinance_client),
):
    ticker_list = list(dict.fromkeys(t.strip().upper() for t in tickers.split(",") if t.strip()))
    if not ticker_list:
        raise HTTPException(status_code=422, detail="tickers must contain at least one ticker")
    if len(ticker_list) > 50:
        raise HTTPException(status_code=422, detail="tickers must contain at most 50 tickers")
    return {"quotes": get_price_changes(ticker_list, yf_client)}
