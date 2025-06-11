from typing import Any
from urllib.parse import urlencode
import os
from pydantic import BaseModel
from dataclasses import dataclass
from datetime import datetime
from connectors.database import SessionLocal

from sqlalchemy.inspection import inspect

from models.company_fundamental import CompanyFundamental

@dataclass(frozen=True)
class CompanyFundamentalDto:
    name: str
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
    logo_url: str | None

@dataclass(frozen=True)
class CompanyConnector:
    def _to_dict(self, model_instance) -> dict[str, Any]:
        """Convert SQLAlchemy model to dictionary, handling datetime fields"""
        result = {}
        for c in inspect(model_instance).mapper.column_attrs:
            value = getattr(model_instance, c.key)
            # Convert datetime objects to ISO format strings
            if isinstance(value, datetime):
                value = value.isoformat()
            result[c.key] = value
        return result
    
    def get_fundamental_data(self, ticker: str) -> CompanyFundamentalDto | None:
        with SessionLocal() as db:
            data = db.query(CompanyFundamental).filter(CompanyFundamental.company_symbol == ticker).first()
            if not data:
                return None
            
            return CompanyFundamentalDto(**data.data)
        
    def persist_fundamental_data(self, ticker: str, data: CompanyFundamentalDto) -> CompanyFundamentalDto:
        with SessionLocal() as db:
            fundamental_data = CompanyFundamental(
                company_symbol=ticker,
                data=data.__dict__
            )
            db.add(fundamental_data)
            db.commit()
            db.refresh(fundamental_data)

            return CompanyFundamentalDto(**self._to_dict(fundamental_data).get("data"))


def get_company_logo_url(company_name: str):
    """
    Proxy endpoint to fetch company logo and return as image response
    """
    API_KEY = os.getenv('BRAND_FETCH_API_KEY')
    params = urlencode({'c': API_KEY })
    return f"https://cdn.brandfetch.io/{company_name.lower()}.com/w/100/h/100?{params}"

def get_company_logo_url_from_ticker(ticker: str):
    """
    Get company logo URL from ticker
    """
    company = get_by_ticker(ticker)
    if company:
        return company.logo_url
    return None

class Company(BaseModel):
    name: str
    ticker: str
    logo_url: str

def get_all() -> list[Company]:
    # Return hard-coded data for now. Move to DB later
    return [
      Company(name="Apple", ticker="AAPL", logo_url=get_company_logo_url("apple")),
      Company(name="Tesla", ticker="TSLA", logo_url=get_company_logo_url("tesla")),
      Company(name="Microsoft", ticker="MSFT", logo_url=get_company_logo_url("microsoft")),
      Company(name="Nvidia", ticker="NVDA", logo_url=get_company_logo_url("nvidia")),
      Company(name="Nordea", ticker="NDA-FI.HE", logo_url=get_company_logo_url("nordea")),
      Company(name="Mandatum", ticker="MANTA.HE", logo_url=get_company_logo_url("mandatum")),
      Company(name="Fortum", ticker="FORTUM.HE", logo_url=get_company_logo_url("fortum")),
      Company(name="Alphabet", ticker="GOOG", logo_url=get_company_logo_url("google")),
      Company(name="Amazon", ticker="AMZN", logo_url=get_company_logo_url("amazon")),
      Company(name="Meta", ticker="META", logo_url=get_company_logo_url("meta")),
      Company(name="Netflix", ticker="NFLX", logo_url=get_company_logo_url("netflix")),
      Company(name="Berkshire Hathaway", ticker="BRK.A", logo_url=get_company_logo_url("berkshire")),
      Company(name="Wallmart", ticker="WMT", logo_url=get_company_logo_url("walmart")),
      Company(name="AT&T", ticker="T", logo_url=get_company_logo_url("att")),
      Company(name="Coca Cola", ticker="KO", logo_url=get_company_logo_url("coca-cola")),
      Company(name="ASML Holding", ticker="ASML", logo_url=get_company_logo_url("asml")),
      Company(name="DoorDash", ticker="DASH", logo_url=get_company_logo_url("doordash")),
      Company(name="SnowFlake", ticker="SNOW", logo_url=get_company_logo_url("snowflake")),
      Company(name="Upwork", ticker="UPWK", logo_url=get_company_logo_url("upwork"))
    ]

def get_by_ticker(ticker: str) -> Company | None:
  all_companies = get_all()
  company = [item for item in all_companies if item.ticker == ticker.upper()]
  if len(company) == 0:
    return None

  return company[0]
