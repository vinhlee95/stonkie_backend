import asyncio
import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ai_models.model_mapper import map_frontend_model_to_enum
from api.deps import get_current_user
from connectors.user import UserDto
from services.portfolio import (
    MAX_HOLDINGS_PER_USER,
    MAX_LOTS_PER_HOLDING,
    HoldingLimitError,
    LotLimitError,
    QuoteUnavailableError,
    UnknownTickerError,
)
from services.portfolio_chat import ScopeNotInPortfolioError
from services.portfolio_service import PortfolioService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/me/portfolio", tags=["portfolio"])

TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-=^]{0,19}$")


def get_portfolio_service() -> PortfolioService:
    return PortfolioService()


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


class ChatIn(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    scopeTicker: str | None = Field(default=None, max_length=32)
    conversationId: str | None = Field(default=None, max_length=100)
    preferredModel: str = Field(default="fastest", max_length=50)


def _normalise_ticker(ticker: str) -> str:
    normalised = ticker.strip().upper()
    if not TICKER_RE.match(normalised):
        raise HTTPException(status_code=422, detail="Invalid ticker")
    return normalised


@router.get("")
def read_portfolio(
    user: UserDto = Depends(get_current_user),
    service: PortfolioService = Depends(get_portfolio_service),
):
    return service.get_portfolio(user.id)


@router.get("/performance")
def read_performance(
    user: UserDto = Depends(get_current_user),
    service: PortfolioService = Depends(get_portfolio_service),
):
    return service.get_performance(user.id)


@router.post("/holdings/{ticker}/lots", status_code=status.HTTP_201_CREATED)
def post_lot(
    ticker: str,
    body: LotIn,
    user: UserDto = Depends(get_current_user),
    service: PortfolioService = Depends(get_portfolio_service),
):
    ticker = _normalise_ticker(ticker)
    try:
        return service.add_lot(
            user_id=user.id,
            ticker=ticker,
            name=body.name,
            shares=body.shares,
            price=body.price,
            purchased_on=body.purchased_on,
        )
    except UnknownTickerError:
        raise HTTPException(status_code=422, detail=f"No price data for {ticker}")
    except QuoteUnavailableError:
        raise HTTPException(status_code=503, detail=f"Price data for {ticker} is temporarily unavailable")
    except HoldingLimitError:
        raise HTTPException(status_code=409, detail=f"Portfolio is limited to {MAX_HOLDINGS_PER_USER} holdings")
    except LotLimitError:
        raise HTTPException(status_code=409, detail=f"A holding is limited to {MAX_LOTS_PER_HOLDING} lots")


@router.patch("/lots/{lot_id}")
def patch_lot(
    lot_id: UUID,
    body: LotPatch,
    user: UserDto = Depends(get_current_user),
    service: PortfolioService = Depends(get_portfolio_service),
):
    lot = service.update_lot(user_id=user.id, lot_id=lot_id, changes=body.model_dump(exclude_unset=True))
    if lot is None:
        raise HTTPException(status_code=404, detail="Lot not found")
    return lot


@router.delete("/lots/{lot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lot(
    lot_id: UUID,
    user: UserDto = Depends(get_current_user),
    service: PortfolioService = Depends(get_portfolio_service),
):
    if not service.remove_lot(user_id=user.id, lot_id=lot_id):
        raise HTTPException(status_code=404, detail="Lot not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/holdings/{ticker}", status_code=status.HTTP_204_NO_CONTENT)
def delete_holding(
    ticker: str,
    user: UserDto = Depends(get_current_user),
    service: PortfolioService = Depends(get_portfolio_service),
):
    if not service.remove_holding(user_id=user.id, ticker=_normalise_ticker(ticker)):
        raise HTTPException(status_code=404, detail="Holding not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/chat")
async def chat(
    body: ChatIn,
    request: Request,
    user: UserDto = Depends(get_current_user),
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
) -> StreamingResponse:
    service = portfolio_service.chat()
    if not await service.allow_request(user.id):
        raise HTTPException(status_code=429, detail="Too many portfolio chat requests, try again in a minute")
    try:
        scope_ticker = await service.resolve_scope(user.id, body.scopeTicker)
    except ScopeNotInPortfolioError as exc:
        raise HTTPException(status_code=422, detail=f"{exc} is not in your portfolio")

    async def generate():
        try:
            async for event in service.stream(
                user_id=user.id,
                question=body.question,
                scope_ticker=scope_ticker,
                preferred_model=map_frontend_model_to_enum(body.preferredModel),
                conversation_id=body.conversationId,
                is_disconnected=request.is_disconnected,
            ):
                yield json.dumps(event) + "\n\n"
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Portfolio chat stream failed")
            yield json.dumps({"type": "error", "code": "internal", "body": "Something went wrong"}) + "\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "private, no-store"})
