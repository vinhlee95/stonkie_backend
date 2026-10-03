"""yfinance connector for daily price history and live quotes."""

import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFDataException, YFRateLimitError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveQuoteDto:
    """Latest regular-session price. Yahoo delays some exchanges by 15-20 min."""

    price: float
    prev_close: float
    currency: str | None
    market_time: datetime
    trading_date: date


@dataclass(frozen=True)
class TickerSearchQuoteDto:
    """One Yahoo symbol-search match."""

    symbol: str
    name: str | None
    exchange: str | None
    quote_type: str | None
    is_yahoo_finance: bool


class YahooSearchUnavailableError(Exception):
    """Yahoo search is down, rate-limited, timing out or unreachable (not a per-query failure)."""


# Transport failures, named as yfinance does (curl_cffi and requests share these names).
_TRANSPORT_ERROR_NAMES = {"Timeout", "ConnectTimeout", "ReadTimeout", "ConnectionError", "RemoteDisconnected"}


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

    def get_close_history_batch(self, symbols: list[str]) -> dict[str, pd.Series]:
        """5y of daily closes per symbol in one batched download, indexed by session date (tz-naive).
        Symbols Yahoo has no data for are omitted."""
        frame = yf.download(
            symbols, period="5y", interval="1d", auto_adjust=False, group_by="ticker", threads=True, progress=False
        )
        return parse_close_batch(frame, symbols)

    def get_live_quote(self, ticker: str) -> LiveQuoteDto | None:
        """One chart request: hourly bars fill the history metadata (price, time, currency) in the
        same response, so get_history_metadata() does not refetch."""
        yf_ticker = yf.Ticker(ticker)
        bars = yf_ticker.history(period="5d", interval="1h", auto_adjust=False)
        return parse_live_quote(yf_ticker.get_history_metadata(), bars)

    def search(self, query: str) -> list[TickerSearchQuoteDto]:
        """Yahoo symbol search. Raises YahooSearchUnavailableError for outage-type failures."""
        try:
            quotes = yf.Search(query, max_results=10, news_count=0, lists_count=0, timeout=3, raise_errors=True).quotes
        except Exception as exc:
            if _is_search_outage(exc):
                raise YahooSearchUnavailableError(str(exc)) from exc
            raise
        return [dto for q in quotes if (dto := parse_search_quote(q)) is not None]

    def get_info(self, ticker: str) -> dict:
        """Yahoo quoteSummary profile (sector, country, quoteType, ...). Slow: one request per ticker."""
        return yf.Ticker(ticker).info or {}

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


def parse_search_quote(quote: dict) -> TickerSearchQuoteDto | None:
    symbol = quote.get("symbol")
    if not symbol:
        return None
    return TickerSearchQuoteDto(
        symbol=symbol,
        name=quote.get("longname") or quote.get("shortname"),
        exchange=quote.get("exchDisp"),
        quote_type=quote.get("quoteType"),
        is_yahoo_finance=bool(quote.get("isYahooFinance")),
    )


def parse_close_batch(frame: pd.DataFrame | None, symbols: list[str]) -> dict[str, pd.Series]:
    """Close column per symbol from a group_by="ticker" download. The frame shares one date index
    across symbols, so each column's NaNs (non-trading days, unknown symbols) are dropped."""
    if frame is None or frame.empty or not isinstance(frame.columns, pd.MultiIndex):
        return {}
    present = set(frame.columns.get_level_values(0))
    closes = {}
    for symbol in symbols:
        if symbol not in present or "Close" not in frame[symbol]:
            continue
        series = frame[symbol]["Close"].dropna()
        if not series.empty:
            closes[symbol] = series
    return closes


def _is_search_outage(exc: Exception) -> bool:
    # Non-JSON body = Yahoo error page (5xx/maintenance), not a problem with the query.
    if isinstance(exc, (YFDataException, YFRateLimitError, TimeoutError, ConnectionError, json.JSONDecodeError)):
        return True
    return type(exc).__name__ in _TRANSPORT_ERROR_NAMES


def parse_live_quote(meta: dict, bars: pd.DataFrame) -> LiveQuoteDto | None:
    price = meta.get("regularMarketPrice")
    market_time = _to_utc(meta.get("regularMarketTime"))
    if not _is_positive(price) or market_time is None:
        return None
    tz = _exchange_tz(meta, bars)
    trading_date = market_time.astimezone(tz).date()
    # Official prior close; the last hourly bar can differ from the closing auction.
    prev_close = meta.get("previousClose")
    if not _is_positive(prev_close):
        prev_close = _prior_day_close(bars, trading_date)
    if not _is_positive(prev_close):
        return None
    return LiveQuoteDto(
        price=float(price),
        prev_close=float(prev_close),
        currency=meta.get("currency"),
        market_time=market_time,
        trading_date=trading_date,
    )


def _to_utc(value) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, (int, float)) and math.isfinite(value):
        return datetime.fromtimestamp(value, UTC)
    return None


def _exchange_tz(meta: dict, bars: pd.DataFrame):
    name = meta.get("exchangeTimezoneName")
    if name:
        return ZoneInfo(name)
    if not bars.empty and bars.index.tz is not None:
        return bars.index.tz
    return UTC


def _prior_day_close(bars: pd.DataFrame, trading_date: date) -> float | None:
    if bars.empty:
        return None
    closes = bars["Close"][[d < trading_date for d in bars.index.date]].dropna()
    return float(closes.iloc[-1]) if not closes.empty else None


def _is_positive(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0
