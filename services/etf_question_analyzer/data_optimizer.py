"""ETF data optimizer for efficient data fetching."""

import logging
from typing import Optional

from connectors.etf_fundamental import ETFFundamentalConnector, ETFFundamentalDto

from .types import ETFDataRequirement

logger = logging.getLogger(__name__)


class ETFDataOptimizer:
    """Optimizes ETF data fetching based on question requirements."""

    def __init__(self, connector: Optional[ETFFundamentalConnector] = None):
        """
        Initialize the optimizer.

        Args:
            connector: ETF connector instance. Creates default if not provided.
        """
        self.connector = connector or ETFFundamentalConnector()

    async def fetch_optimized_data(
        self, ticker: str, data_requirement: ETFDataRequirement
    ) -> Optional[ETFFundamentalDto]:
        """
        Fetch ETF data optimized for the data requirement level.

        Args:
            ticker: ETF ticker symbol
            data_requirement: Level of data needed (NONE, BASIC, DETAILED)

        Returns:
            ETFFundamentalDto or None based on requirement
        """
        # NONE: No data needed
        if data_requirement == ETFDataRequirement.NONE:
            logger.info(f"Data requirement NONE for {ticker} - skipping fetch")
            return None

        # Normalize ticker
        if not ticker or ticker.upper() in ["UNDEFINED", "NULL", "NONE"]:
            logger.warning("Invalid ticker for data fetch")
            return None

        # Fetch full DTO from database
        try:
            etf_data = self.connector.get_by_ticker(ticker)

            if not etf_data:
                logger.warning(f"ETF not found in database: {ticker}")
                return None

            # BASIC: Return DTO (context builder will extract core_metadata)
            if data_requirement == ETFDataRequirement.BASIC:
                logger.info(f"Fetched BASIC data for {ticker}")
                return etf_data

            # DETAILED: Return full DTO with holdings/sectors/countries
            if data_requirement == ETFDataRequirement.DETAILED:
                # Log data completeness
                has_holdings = bool(etf_data.holdings)
                has_sectors = bool(etf_data.sector_allocation)
                has_countries = bool(etf_data.country_allocation)

                logger.info(
                    f"Fetched DETAILED data for {ticker}: "
                    f"holdings={has_holdings}, sectors={has_sectors}, countries={has_countries}"
                )

                return etf_data

            return etf_data

        except Exception as e:
            logger.error(f"Error fetching ETF data for {ticker}: {e}")
            return None
