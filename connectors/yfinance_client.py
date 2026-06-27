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

    def get_quote(self, ticker: str) -> dict | None:
        """Live quote snapshot used as a fallback when the latest daily bar's
        Close is missing. Yahoo populates these even when the daily chart's
        Close column lags with a NaN."""
        try:
            fast_info = yf.Ticker(ticker).fast_info
            last_price = fast_info.get("lastPrice")
            prev_close = fast_info.get("regularMarketPreviousClose")
        except Exception:
            logger.warning("Failed to fetch quote for %s", ticker, exc_info=True)
            return None
        if last_price is None and prev_close is None:
            return None
        return {"last_price": last_price, "prev_close": prev_close}
