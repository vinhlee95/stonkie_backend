"""Portfolio feature. Routers import only from here: PortfolioService is the entry point; every other
module in this package is a private helper of it."""

from services.portfolio.chat import ChatScope
from services.portfolio.errors import (
    HoldingLimitError,
    LotLimitError,
    PortfolioUnavailableError,
    QuoteUnavailableError,
    ScopeNotInPortfolioError,
    UnknownTickerError,
)
from services.portfolio.service import PortfolioService
from services.portfolio.valuation import MAX_HOLDINGS_PER_USER, MAX_LOTS_PER_HOLDING

__all__ = [
    "MAX_HOLDINGS_PER_USER",
    "MAX_LOTS_PER_HOLDING",
    "ChatScope",
    "HoldingLimitError",
    "LotLimitError",
    "PortfolioService",
    "PortfolioUnavailableError",
    "QuoteUnavailableError",
    "ScopeNotInPortfolioError",
    "UnknownTickerError",
]
