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
from services.portfolio.news import DEFAULT_LIMIT as NEWS_DEFAULT_LIMIT
from services.portfolio.news import MAX_LIMIT as NEWS_MAX_LIMIT
from services.portfolio.service import PortfolioService
from services.portfolio.valuation import MAX_HOLDINGS_PER_USER, MAX_LOTS_PER_HOLDING

__all__ = [
    "MAX_HOLDINGS_PER_USER",
    "MAX_LOTS_PER_HOLDING",
    "NEWS_DEFAULT_LIMIT",
    "NEWS_MAX_LIMIT",
    "ChatScope",
    "HoldingLimitError",
    "LotLimitError",
    "PortfolioService",
    "PortfolioUnavailableError",
    "QuoteUnavailableError",
    "ScopeNotInPortfolioError",
    "UnknownTickerError",
]
