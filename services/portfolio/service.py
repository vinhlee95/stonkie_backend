"""PortfolioService: the only entry point for the portfolio router.

It constructs every I/O collaborator (`x or XConnector()`) and hands them to the helper modules in
this package, which never construct connectors themselves and are never imported from outside it.
"""

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from datetime import date
from typing import Any
from uuid import UUID

from langfuse import observe

from ai_models.model_name import ModelName
from connectors.brave_client import BraveClient
from connectors.company import CompanyConnector
from connectors.fx import FxConnector
from connectors.portfolio import LotDto, PortfolioConnector
from connectors.yfinance_client import YFinanceClient
from services.portfolio import chat, valuation
from services.portfolio.chat import ChatScope
from services.portfolio.errors import PortfolioUnavailableError
from services.portfolio.performance import get_performance

logger = logging.getLogger(__name__)


def _lot_out(lot: LotDto) -> dict:
    return {"ticker": lot.ticker, **valuation.lot_to_dict(lot)}


class PortfolioService:
    def __init__(
        self,
        portfolio: PortfolioConnector | None = None,
        yf_client: YFinanceClient | None = None,
        brave_client: BraveClient | None = None,
        fx: FxConnector | None = None,
        companies: CompanyConnector | None = None,
    ) -> None:
        self._portfolio = portfolio or PortfolioConnector()
        self._yf_client = yf_client or YFinanceClient()
        # Only chat needs Brave; built on first use so other endpoints don't open an HTTP client.
        self._brave_client = brave_client
        self._fx = fx or FxConnector(self._yf_client)
        self._companies = companies or CompanyConnector()

    def _brave(self) -> BraveClient:
        if self._brave_client is None:
            self._brave_client = BraveClient(api_key=os.getenv("BRAVE_API_KEY", ""))
        return self._brave_client

    # --- Holdings -------------------------------------------------------------------------------

    def get_portfolio(self, user_id: str) -> dict:
        return valuation.get_portfolio(
            user_id, self._portfolio, self._yf_client, fx=self._fx, companies=self._companies
        )

    def get_performance(self, user_id: str) -> dict:
        return get_performance(user_id, self._portfolio, self._yf_client)

    def add_lot(
        self, *, user_id: str, ticker: str, name: str | None, shares: float, price: float, purchased_on: date | None
    ) -> dict:
        """Raises the lot errors in services.portfolio.errors (unknown ticker, quote unavailable, limits)."""
        lot = valuation.add_lot(
            user_id=user_id,
            ticker=ticker,
            name=name,
            shares=shares,
            price=price,
            purchased_on=purchased_on,
            portfolio=self._portfolio,
            yf_client=self._yf_client,
        )
        return _lot_out(lot)

    def update_lot(self, *, user_id: str, lot_id: UUID, changes: dict) -> dict | None:
        lot = valuation.update_lot(user_id=user_id, lot_id=lot_id, changes=changes, portfolio=self._portfolio)
        return _lot_out(lot) if lot is not None else None

    def remove_lot(self, *, user_id: str, lot_id: UUID) -> bool:
        return valuation.remove_lot(user_id=user_id, lot_id=lot_id, portfolio=self._portfolio)

    def remove_holding(self, *, user_id: str, ticker: str) -> bool:
        return valuation.remove_holding(user_id=user_id, ticker=ticker, portfolio=self._portfolio)

    # --- Chat -----------------------------------------------------------------------------------

    async def allow_chat(self, user_id: str) -> bool:
        """False when the user is over the per-minute chat limit."""
        return await asyncio.to_thread(chat.allow_request, user_id)

    async def resolve_chat_scope(self, user_id: str, scope_ticker: str | None) -> ChatScope:
        """Lists the holdings once for the whole request. Raises ScopeNotInPortfolioError for a focus
        ticker the user doesn't hold, PortfolioUnavailableError when the holdings can't be listed."""
        try:
            holdings = await asyncio.to_thread(self._portfolio.list_holdings, user_id)
        except Exception as exc:
            raise PortfolioUnavailableError(user_id) from exc
        return chat.resolve_scope(holdings, scope_ticker)

    @observe(
        name="portfolio_chat.stream",
        as_type="generation",
        # Private: answers quote the user's holdings and values. Route/scope go in metadata instead.
        capture_input=False,
        capture_output=False,
    )
    async def stream_chat(
        self,
        *,
        user_id: str,
        question: str,
        scope: ChatScope,
        preferred_model: ModelName,
        conversation_id: str | None,
        is_disconnected,
    ) -> AsyncGenerator[dict[str, Any], None]:
        async with chat.in_flight_slot() as admitted:
            if not admitted:
                yield chat.BUSY_ERROR
                return
            try:
                async for event in chat.answer_stream(
                    portfolio=self._portfolio,
                    yf_client=self._yf_client,
                    fx=self._fx,
                    companies=self._companies,
                    brave_client=self._brave(),
                    user_id=user_id,
                    question=question,
                    scope=scope,
                    preferred_model=preferred_model,
                    conversation_id=conversation_id,
                    is_disconnected=is_disconnected,
                ):
                    yield event
            except Exception:
                # Caught here, inside @observe: langfuse's wrapper swallows exceptions raised by async
                # generators, so the router would never see them and the client would get a silent cut-off.
                logger.exception("Portfolio chat stream failed")
                yield chat.INTERNAL_ERROR
