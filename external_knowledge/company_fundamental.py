import os

import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()
import logging

logger = logging.getLogger(__name__)

ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")

# Cache to store company fundamentals
_company_fundamental_cache = {}


def _get_from_alpha_vantage(ticker: str) -> dict | None:
    try:
        url = f"https://www.alphavantage.co/query?function=OVERVIEW&symbol={ticker}&apikey={ALPHA_VANTAGE_API_KEY}"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()

        if not data or not data.get("Name") or not data.get("MarketCapitalization"):
            logger.warning("Alpha Vantage returned no data", extra={"ticker": ticker, "data": data})
            return None

        return data
    except Exception as ex:
        logger.error("Alpha Vantage fetch failed", extra={"ticker": ticker, "error": str(ex)})
        return None


def _get_from_yfinance(ticker: str) -> dict | None:
    try:
        info = yf.Ticker(ticker).info
        name = info.get("longName") or info.get("shortName")
        market_cap = info.get("marketCap")

        if not name or not market_cap:
            logger.warning("yfinance returned no data", extra={"ticker": ticker})
            return None

        shares_outstanding = info.get("sharesOutstanding", 0)
        eps = info.get("trailingEps", 0) or 0
        # Normalise to Alpha Vantage key names so callers need no changes
        return {
            "Name": name,
            "MarketCapitalization": str(market_cap),
            "PERatio": str(info.get("trailingPE") or ""),
            "RevenueTTM": str(info.get("totalRevenue") or 0),
            "EPS": str(eps),
            "SharesOutstanding": str(shares_outstanding),
            "DividendYield": str(info.get("dividendYield") or 0),
            "Sector": info.get("sector") or "",
            "Industry": info.get("industry") or "",
            "Description": info.get("longBusinessSummary") or "",
            "Country": info.get("country") or "",
            "Exchange": info.get("exchange") or "",
        }
    except Exception as ex:
        logger.error("yfinance fetch failed", extra={"ticker": ticker, "error": str(ex)})
        return None


def get_company_fundamental(ticker: str) -> dict | None:
    logger.info("Get fundamental data for ticker", extra={"ticker": ticker})

    if ticker in _company_fundamental_cache:
        logger.info("Found cached data", extra={"ticker": ticker, "cached": True})
        return _company_fundamental_cache[ticker]

    data = _get_from_alpha_vantage(ticker)
    if data:
        logger.info("Fetched fundamental data from Alpha Vantage", extra={"ticker": ticker})
        _company_fundamental_cache[ticker] = data
        return data

    logger.info("Falling back to yfinance", extra={"ticker": ticker})
    data = _get_from_yfinance(ticker)
    if data:
        logger.info("Fetched fundamental data from yfinance", extra={"ticker": ticker})
        _company_fundamental_cache[ticker] = data
        return data

    logger.error("All sources failed for ticker", extra={"ticker": ticker})
    return None
