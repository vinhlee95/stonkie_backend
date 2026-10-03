import re
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from api.deps import get_current_user
from connectors.portfolio import LotDto, PortfolioConnector
from connectors.user import UserDto
from connectors.yfinance_client import YFinanceClient
from services.portfolio import (
    MAX_HOLDINGS_PER_USER,
    MAX_LOTS_PER_HOLDING,
    HoldingLimitError,
    LotLimitError,
    QuoteUnavailableError,
    UnknownTickerError,
    add_lot,
    get_portfolio,
    lot_to_dict,
    remove_holding,
    remove_lot,
    update_lot,
)
from services.portfolio_performance import get_performance

router = APIRouter(prefix="/api/me/portfolio", tags=["portfolio"])

TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-=^]{0,19}$")


def get_portfolio_connector() -> PortfolioConnector:
    return PortfolioConnector()


def get_yfinance_client() -> YFinanceClient:
    return YFinanceClient()


EARLIEST_PURCHASE_DATE = date(1900, 1, 1)


def _check_purchase_date(value: date | None) -> date | None:
    # One day of slack: a user east of UTC can already be on tomorrow's date.
    latest = datetime.now(UTC).date() + timedelta(days=1)
    if value is not None and not EARLIEST_PURCHASE_DATE <= value <= latest:
        raise ValueError("purchased_on must be between 1900-01-01 and tomorrow (UTC)")
    return value


PurchaseDate = Annotated[date | None, AfterValidator(_check_purchase_date)]


class LotIn(BaseModel):
    # ge=1e-6 matches the Numeric(20, 6) column scale, so no accepted value rounds to 0.
    shares: float = Field(ge=1e-6, lt=1e12)
    price: float = Field(ge=1e-6, lt=1e12)
    purchased_on: PurchaseDate = None
    name: str | None = Field(default=None, max_length=200)


class LotPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shares: float | None = Field(default=None, ge=1e-6, lt=1e12)
    price: float | None = Field(default=None, ge=1e-6, lt=1e12)
    purchased_on: PurchaseDate = None

    @model_validator(mode="after")
    def _check_fields(self) -> "LotPatch":
        if not self.model_fields_set:
            raise ValueError("Nothing to update")
        # shares/price may be omitted but not cleared: the columns are NOT NULL.
        for field in ("shares", "price"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


def _lot_out(lot: LotDto) -> dict:
    return {"ticker": lot.ticker, **lot_to_dict(lot)}


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


@router.get("/performance")
def read_performance(
    user: UserDto = Depends(get_current_user),
    portfolio: PortfolioConnector = Depends(get_portfolio_connector),
    yf_client: YFinanceClient = Depends(get_yfinance_client),
):
    return get_performance(user.id, portfolio, yf_client)


@router.post("/holdings/{ticker}/lots", status_code=status.HTTP_201_CREATED)
def post_lot(
    ticker: str,
    body: LotIn,
    user: UserDto = Depends(get_current_user),
    portfolio: PortfolioConnector = Depends(get_portfolio_connector),
    yf_client: YFinanceClient = Depends(get_yfinance_client),
):
    ticker = _normalise_ticker(ticker)
    try:
        lot = add_lot(
            user_id=user.id,
            ticker=ticker,
            name=body.name,
            shares=body.shares,
            price=body.price,
            purchased_on=body.purchased_on,
            portfolio=portfolio,
            yf_client=yf_client,
        )
    except UnknownTickerError:
        raise HTTPException(status_code=422, detail=f"No price data for {ticker}")
    except QuoteUnavailableError:
        raise HTTPException(status_code=503, detail=f"Price data for {ticker} is temporarily unavailable")
    except HoldingLimitError:
        raise HTTPException(status_code=409, detail=f"Portfolio is limited to {MAX_HOLDINGS_PER_USER} holdings")
    except LotLimitError:
        raise HTTPException(status_code=409, detail=f"A holding is limited to {MAX_LOTS_PER_HOLDING} lots")
    return _lot_out(lot)


@router.patch("/lots/{lot_id}")
def patch_lot(
    lot_id: UUID,
    body: LotPatch,
    user: UserDto = Depends(get_current_user),
    portfolio: PortfolioConnector = Depends(get_portfolio_connector),
):
    lot = update_lot(user_id=user.id, lot_id=lot_id, changes=body.model_dump(exclude_unset=True), portfolio=portfolio)
    if lot is None:
        raise HTTPException(status_code=404, detail="Lot not found")
    return _lot_out(lot)


@router.delete("/lots/{lot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lot(
    lot_id: UUID,
    user: UserDto = Depends(get_current_user),
    portfolio: PortfolioConnector = Depends(get_portfolio_connector),
):
    if not remove_lot(user_id=user.id, lot_id=lot_id, portfolio=portfolio):
        raise HTTPException(status_code=404, detail="Lot not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/holdings/{ticker}", status_code=status.HTTP_204_NO_CONTENT)
def delete_holding(
    ticker: str,
    user: UserDto = Depends(get_current_user),
    portfolio: PortfolioConnector = Depends(get_portfolio_connector),
):
    if not remove_holding(user_id=user.id, ticker=_normalise_ticker(ticker), portfolio=portfolio):
        raise HTTPException(status_code=404, detail="Holding not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
