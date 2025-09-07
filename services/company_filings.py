from typing import List, Dict, Any
from connectors.company_financial import CompanyFinancialConnector

company_financial_connector = CompanyFinancialConnector()

def get_company_filings(ticker: str, period: str) -> List[Dict[str, Any]]:
    """
    Service method to get company filings for a given ticker and period
    
    Args:
        ticker: Company ticker symbol
        period: Period type ('annual' or 'quarterly')
    
    Returns:
        List of filing objects with url and period_end_year
    """
    return company_financial_connector.get_company_filings(ticker, period)
