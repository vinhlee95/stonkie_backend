"""What the portfolio API calls: owns the portfolio connector and the Yahoo client, so the router
never builds or passes I/O collaborators itself."""

from datetime import date
from uuid import UUID

from connectors.portfolio import LotDto, PortfolioConnector
from connectors.yfinance_client import YFinanceClient
from services import portfolio as portfolio_ops
from services.portfolio_chat import PortfolioChatStreamService
from services.portfolio_performance import get_performance


def _lot_out(lot: LotDto) -> dict:
    return {"ticker": lot.ticker, **portfolio_ops.lot_to_dict(lot)}


class PortfolioService:
    def __init__(self, portfolio: PortfolioConnector | None = None, yf_client: YFinanceClient | None = None) -> None:
        self._portfolio = portfolio or PortfolioConnector()
        self._yf_client = yf_client or YFinanceClient()

    def get_portfolio(self, user_id: str) -> dict:
        return portfolio_ops.get_portfolio(user_id, self._portfolio, self._yf_client)

    def get_performance(self, user_id: str) -> dict:
        return get_performance(user_id, self._portfolio, self._yf_client)

    def add_lot(
        self, *, user_id: str, ticker: str, name: str | None, shares: float, price: float, purchased_on: date | None
    ) -> dict:
        """Raises the services.portfolio lot errors (unknown ticker, quote unavailable, limits)."""
        lot = portfolio_ops.add_lot(
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
        lot = portfolio_ops.update_lot(user_id=user_id, lot_id=lot_id, changes=changes, portfolio=self._portfolio)
        return _lot_out(lot) if lot is not None else None

    def remove_lot(self, *, user_id: str, lot_id: UUID) -> bool:
        return portfolio_ops.remove_lot(user_id=user_id, lot_id=lot_id, portfolio=self._portfolio)

    def remove_holding(self, *, user_id: str, ticker: str) -> bool:
        return portfolio_ops.remove_holding(user_id=user_id, ticker=ticker, portfolio=self._portfolio)

    def chat(self) -> PortfolioChatStreamService:
        return PortfolioChatStreamService(self._portfolio, self._yf_client)
