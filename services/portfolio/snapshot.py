"""Everything Portfolio chat knows about a user's portfolio. PortfolioService loads it."""

import logging
from dataclasses import dataclass, field
from datetime import date

from services.portfolio.performance import EurSeries, period_returns
from services.portfolio.risk import compute_risk

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


def risk_and_returns(series: EurSeries | None, rows: list[dict]) -> tuple[dict | None, dict | None]:
    """Risk and period returns; each falls back to None on its own."""
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
