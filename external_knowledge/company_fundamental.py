import os

import requests
from dotenv import load_dotenv

load_dotenv()
import logging

logger = logging.getLogger(__name__)

ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")

# Cache to store company fundamentals
_company_fundamental_cache = {}


def get_company_fundamental(ticker: str) -> dict | None:
    logger.info("Get fundamental data for ticker", {"ticker": ticker})
    try:
        # Check if data is in cache
        if ticker in _company_fundamental_cache:
            logger.info("Found cached data", _company_fundamental_cache[ticker])
            return _company_fundamental_cache[ticker]

        url = f"https://www.alphavantage.co/query?function=OVERVIEW&symbol={ticker}&apikey={ALPHA_VANTAGE_API_KEY}"
        response = requests.get(url)
        response.raise_for_status()

        data = response.json()

        if not data or not data.get("Name") or not data.get("MarketCapitalization"):
            logger.error(f"Invalid fundamental data found for ticker {ticker}", {"data": data})
            return None

        logger.info(
            "Fetched fundamental data for ticker",
            {
                "ticker": ticker,
                "data": data,
            },
        )

        _company_fundamental_cache[ticker] = data
        return data
    except Exception as ex:
        print(ex)
        logger.error("Failed to fetch company fundamental data", {"error": str(ex)})
