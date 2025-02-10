from external_knowledge.company_fundamental import get_company_fundamental
from pydantic import BaseModel

class CompanyFundamental(BaseModel):
    market_cap: int
    pe_ratio: float
    revenue: int
    net_income: int
    basic_eps: float
    sector: str
    industry: str
    description: str
    country: str
    exchange: str
    dividend_yield: float

def get_key_stats_for_ticker(ticker: str):
    """
    Get key stats for a given ticker symbol
    """
    company_fundamental = get_company_fundamental(ticker)
    
    # Convert MarketCapitalization to int, handle None/None values for dividend yield
    market_cap = int(company_fundamental["MarketCapitalization"]) if company_fundamental["MarketCapitalization"] != "None" else 0
    dividend_yield = float(company_fundamental["DividendYield"]) if company_fundamental["DividendYield"] != "None" else 0.0
    
    return CompanyFundamental(
        market_cap=market_cap,
        pe_ratio=float(company_fundamental["PERatio"]),
        revenue=int(company_fundamental["RevenueTTM"]),
        net_income=int(float(company_fundamental["EPS"]) * float(company_fundamental["SharesOutstanding"])),
        basic_eps=float(company_fundamental["EPS"]),
        sector=company_fundamental["Sector"],
        industry=company_fundamental["Industry"],
        description=company_fundamental["Description"],
        country=company_fundamental["Country"],
        exchange=company_fundamental["Exchange"],
        dividend_yield=dividend_yield
    )
    

