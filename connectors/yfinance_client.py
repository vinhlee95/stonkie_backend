"""yfinance connector for daily price history."""

import logging

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


class YFinanceClient:
    def get_daily_history(self, ticker: str) -> tuple[pd.DataFrame, str | None]:
        yf_ticker = yf.Ticker(ticker)
        history = yf_ticker.history(period="7d", interval="1d", auto_adjust=False)
        currency = None
        try:
            currency = yf_ticker.fast_info.get("currency")
        except Exception:
            logger.warning("Failed to fetch currency for %s", ticker, exc_info=True)
        return history, currency
