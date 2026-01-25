import logging

from connectors.etf_fundamental import ETFFundamentalConnector, ETFFundamentalDto

logger = logging.getLogger(__name__)

etf_connector = ETFFundamentalConnector()


def get_etf_by_ticker(ticker: str) -> ETFFundamentalDto | None:
    """
    Get ETF fundamental data by ticker symbol.

    Args:
        ticker: ETF ticker symbol (e.g., 'SXR8', 'CSPX')

    Returns:
        ETFFundamentalDto if found, None otherwise
    """
    return etf_connector.get_by_ticker(ticker)


async def get_all_etfs() -> list[dict[str, str]]:
    """
    Get all ETFs for display on home page.

    Returns:
        List of dicts with ticker, name, fund_provider fields.
        Returns empty list if no ETFs exist or on database errors.
    """
    try:
        etfs = etf_connector.get_all()
        return [
            {"ticker": etf.ticker, "name": etf.name, "fund_provider": etf.fund_provider} for etf in etfs if etf.ticker
        ]
    except Exception as e:
        logger.error(f"Error fetching all ETFs: {e}")
        return []
