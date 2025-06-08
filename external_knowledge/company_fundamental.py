import requests
from dotenv import load_dotenv
import os
load_dotenv()

ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")

# Cache to store company fundamentals
_company_fundamental_cache = {}

def get_company_fundamental(ticker: str) -> dict | None:
    # Check if data is in cache
    if ticker in _company_fundamental_cache:
        return _company_fundamental_cache[ticker]
        
    url = f'https://www.alphavantage.co/query?function=OVERVIEW&symbol={ticker}&apikey={ALPHA_VANTAGE_API_KEY}'
    response = requests.get(url)
    data = response.json()
    
    _company_fundamental_cache[ticker] = data
    return data

