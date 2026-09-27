import re

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from api.deps import get_current_user
from connectors.portfolio import PortfolioConnector
from connectors.user import UserDto
from connectors.yfinance_client import YFinanceClient
from services.portfolio import UnknownTickerError, get_portfolio, save_holding

router = APIRouter(prefix="/api/me/portfolio", tags=["portfolio"])

TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-=^]{0,19}$")


def get_portfolio_connector() -> PortfolioConnector:
    return PortfolioConnector()


def get_yfinance_client() -> YFinanceClient:
    return YFinanceClient()


class HoldingIn(BaseModel):
    shares: float = Field(gt=0, lt=1e12)
    avg_cost: float = Field(gt=0, lt=1e12)
    name: str | None = Field(default=None, max_length=200)


def _normalise_ticker(ticker: str) -> str:
    normalised = ticker.strip().upper()
    if not TICKER_RE.match(normalised):
        raise HTTPException(status_code=422, detail="Invalid ticker")
    return normalised


@router.get("")
def read_portfolio(
    user: UserDto = Depends(get_current_user),
    portfolio: PortfolioConnector = Depends(get_portfolio_connector),
    yf_client: YFinanceClient = Depends(get_yfinance_client),
):
    return get_portfolio(user.id, portfolio, yf_client)


@router.put("/holdings/{ticker}")
def put_holding(
    ticker: str,
    body: HoldingIn,
    user: UserDto = Depends(get_current_user),
    portfolio: PortfolioConnector = Depends(get_portfolio_connector),
    yf_client: YFinanceClient = Depends(get_yfinance_client),
):
    ticker = _normalise_ticker(ticker)
    try:
        holding = save_holding(
            user_id=user.id,
            ticker=ticker,
            name=body.name,
            shares=body.shares,
            avg_cost=body.avg_cost,
            portfolio=portfolio,
            yf_client=yf_client,
        )
    except UnknownTickerError:
        raise HTTPException(status_code=422, detail=f"No price data for {ticker}")
    return {"ticker": holding.ticker, "name": holding.name, "shares": holding.shares, "avg_cost": holding.avg_cost}


@router.delete("/holdings/{ticker}", status_code=status.HTTP_204_NO_CONTENT)
def delete_holding(
    ticker: str,
    user: UserDto = Depends(get_current_user),
    portfolio: PortfolioConnector = Depends(get_portfolio_connector),
):
    if not portfolio.delete_holding(user_id=user.id, ticker=_normalise_ticker(ticker)):
        raise HTTPException(status_code=404, detail="Holding not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
