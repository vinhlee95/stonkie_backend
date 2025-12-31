"""
Script to fetch SEC filings from Finnhub API.
Focuses on retrieving 10-K annual reports for company analysis.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class FinnhubFilingsClient:
    """Client for fetching SEC filings from Finnhub API."""

    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self):
        """
        Initialize the Finnhub filings client.

        Args:
            api_key: Finnhub API key. If not provided, reads from FINNHUB_API_KEY env var.
        """
        self.api_key = os.getenv("FINNHUB_API_KEY")
        if not self.api_key:
            raise ValueError("FINNHUB_API_KEY not found in environment variables")

    def fetch_filings(
        self,
        symbol: Optional[str] = None,
        cik: Optional[str] = None,
        access_number: Optional[str] = None,
        form: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch SEC filings from Finnhub API.

        Args:
            symbol: Company ticker symbol (e.g., 'AAPL')
            cik: Company CIK number
            access_number: Specific report access number
            form: Form type to filter (e.g., '10-K', 'NT 10-K')
            from_date: Start date in format 'YYYY-MM-DD'
            to_date: End date in format 'YYYY-MM-DD'

        Returns:
            List of filing dictionaries containing filing information
        """
        url = f"{self.BASE_URL}/stock/filings"

        params: Dict[str, str] = {"token": self.api_key}

        if symbol:
            params["symbol"] = symbol
        if cik:
            params["cik"] = cik
        if access_number:
            params["accessNumber"] = access_number
        if form:
            params["form"] = form
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        try:
            logger.info(f"Fetching filings with params: {params}")
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            filings = data if isinstance(data, list) else []

            logger.info(f"Successfully fetched {len(filings)} filings")
            return filings

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching filings from Finnhub: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            raise

    def fetch_10k_filings(
        self, symbol: str, from_date: Optional[str] = None, to_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch 10-K annual report filings for a specific company.

        Args:
            symbol: Company ticker symbol (e.g., 'AAPL')
            from_date: Start date in format 'YYYY-MM-DD'
            to_date: End date in format 'YYYY-MM-DD'

        Returns:
            List of 10-K filing dictionaries
        """
        return self.fetch_filings(symbol=symbol, form="10-K", from_date=from_date, to_date=to_date)

    def fetch_10q_filings(
        self, symbol: str, from_date: Optional[str] = None, to_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch 10-Q quarterly report filings for a specific company.

        Args:
            symbol: Company ticker symbol (e.g., 'AAPL')
            from_date: Start date in format 'YYYY-MM-DD'
            to_date: End date in format 'YYYY-MM-DD'

        Returns:
            List of 10-Q filing dictionaries
        """
        return self.fetch_filings(symbol=symbol, form="10-Q", from_date=from_date, to_date=to_date)


def print_filing_info(filings: List[Dict[str, Any]]) -> None:
    """
    Print formatted filing information.

    Args:
        filings: List of filing dictionaries
    """
    if not filings:
        print("No filings found.")
        return

    print(f"\n{'='*80}")
    print(f"Found {len(filings)} filing(s)")
    print(f"{'='*80}\n")

    for idx, filing in enumerate(filings, 1):
        print(f"Filing #{idx}:")
        print(f"  Form: {filing.get('form', 'N/A')}")
        print(f"  Filed Date: {filing.get('filedDate', 'N/A')}")
        print(f"  Accepted Date: {filing.get('acceptedDate', 'N/A')}")
        print(f"  Access Number: {filing.get('accessNumber', 'N/A')}")
        print(f"  Report URL: {filing.get('reportUrl', 'N/A')}")
        print(f"  Filing URL: {filing.get('filingUrl', 'N/A')}")
        print("-" * 80)
