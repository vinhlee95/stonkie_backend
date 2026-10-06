"""Everything Portfolio chat knows about a user's portfolio, loaded off the event loop."""

import asyncio
import datetime
import logging
from dataclasses import dataclass, field
from datetime import UTC, date

from connectors.company import CompanyConnector
from connectors.fx import FxConnector
from connectors.portfolio import PortfolioConnector
from connectors.yfinance_client import YFinanceClient
from services.portfolio.errors import PortfolioUnavailableError
from services.portfolio.performance import EurSeries, load_eur_series, period_returns
from services.portfolio.risk import compute_risk
from services.portfolio.valuation import get_portfolio, get_quotes

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Everything the chat knows about the portfolio. `returns` / `risk` are None when unavailable."""

    portfolio: dict
    returns: dict | None
    risk: dict | None
    today: date
    # Holdings left out of performance and beta/volatility/drawdown (no price history or currency).
    excluded: list[str] = field(default_factory=list)


async def load_snapshot(
    user_id: str,
    *,
    portfolio: PortfolioConnector,
    yf_client: YFinanceClient,
    fx: FxConnector,
    companies: CompanyConnector,
    holdings: list,
) -> PortfolioSnapshot:
    """Raises PortfolioUnavailableError when the holdings can't be valued; performance and risk
    degrade to None on their own."""
    try:
        # Fetched once and shared, so valuation and history don't both hit Yahoo on a cold cache.
        quotes = await asyncio.to_thread(get_quotes, [h.ticker for h in holdings], yf_client)
    except Exception as exc:
        raise PortfolioUnavailableError(user_id) from exc
    valued, series_result = await asyncio.gather(
        asyncio.to_thread(
            get_portfolio, user_id, portfolio, yf_client, fx=fx, companies=companies, quotes=quotes, holdings=holdings
        ),
        asyncio.to_thread(_safe_series, holdings, yf_client, quotes),
        return_exceptions=True,
    )
    if isinstance(valued, BaseException):
        raise PortfolioUnavailableError(user_id) from valued
    series, excluded = (None, []) if isinstance(series_result, BaseException) else series_result
    risk, returns = await asyncio.to_thread(_risk_and_returns, series, valued["holdings"])
    return PortfolioSnapshot(
        portfolio=valued,
        returns=returns,
        risk=risk,
        today=datetime.datetime.now(UTC).date(),
        excluded=excluded,
    )


def _risk_and_returns(series: EurSeries | None, rows: list[dict]) -> tuple[dict | None, dict | None]:
    """pandas work, so it runs off the event loop. Each falls back to None on its own."""
    try:
        risk = compute_risk(series, rows)
    except Exception:
        logger.exception("Portfolio chat risk failed")
        risk = None
    try:
        returns = period_returns(series) if series is not None else None
    except Exception:
        logger.exception("Portfolio chat returns failed")
        returns = None
    return risk, returns


def _safe_series(holdings: list, yf_client: YFinanceClient, quotes: dict) -> tuple[EurSeries | None, list[str]]:
    """The EUR series and excluded tickers; (None, []) when loading failed (performance and
    beta/vol then show as unavailable while concentration still works)."""
    try:
        return load_eur_series(holdings, yf_client, quotes)
    except Exception:
        logger.exception("Portfolio chat price history failed")
        return None, []
