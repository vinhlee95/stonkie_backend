from urllib.parse import urlencode
import os
from pydantic import BaseModel

class Company(BaseModel):
    name: str
    ticker: str
    logo_url: str

def get_company_logo_url(company_name: str):
    """
    Proxy endpoint to fetch company logo and return as image response
    """
    API_KEY = os.getenv('BRAND_FETCH_API_KEY')
    params = urlencode({'c': API_KEY })
    return f"https://cdn.brandfetch.io/{company_name.lower()}.com/w/100/h/100?{params}"

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
    ]

def get_by_ticker(ticker: str) -> Company | None:
  all_companies = get_all()
  company = [item for item in all_companies if item.ticker == ticker.upper()]
  if len(company) == 0:
    return None

  return company[0]
