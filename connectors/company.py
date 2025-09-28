from typing import Any
from urllib.parse import urlencode
import os
from pydantic import BaseModel
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from connectors.database import SessionLocal
import logging
from sqlalchemy.inspection import inspect
from external_knowledge.company_fundamental import get_company_fundamental
from models.company_fundamental import CompanyFundamental

logger = logging.getLogger(__name__)

class Company(BaseModel):
    name: str
    ticker: str
    logo_url: str = ""

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

def safe_int(value, default=0):
    try:
        if value in [None, "None", ""]:
            return default
        return int(float(value))
    except (ValueError, TypeError):
        return default

def safe_float(value, default=0.0):
    try:
        if value in [None, "None", ""]:
            return default
        return float(value)
    except (ValueError, TypeError):
        return default

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

    def _get_fresh_company_data(self, ticker: str) -> CompanyFundamentalDto | None:
        company_fundamental = get_company_fundamental(ticker)
        if not company_fundamental:
            logger.info(f"No company fundamental data found from external source: {ticker}")
            return None
        
        # Persist data
        market_cap = safe_int(company_fundamental.get("MarketCapitalization"))
        dividend_yield = safe_float(company_fundamental.get("DividendYield"))
        pe_ratio = safe_float(company_fundamental.get("PERatio"))
        revenue = safe_int(company_fundamental.get("RevenueTTM"))
        eps = safe_float(company_fundamental.get("EPS"))
        shares_outstanding = safe_float(company_fundamental.get("SharesOutstanding"))
        net_income = safe_int(eps * shares_outstanding)
        company_name = company_fundamental.get("Name", "")

        return CompanyFundamentalDto(
            name=company_name,
            market_cap=market_cap,
            pe_ratio=pe_ratio,
            revenue=revenue,
            net_income=net_income,
            basic_eps=eps,
            sector=company_fundamental.get("Sector", ""),
            industry=company_fundamental.get("Industry", ""),
            description=company_fundamental.get("Description", ""),
            country=company_fundamental.get("Country", ""),
            exchange=company_fundamental.get("Exchange", ""),
            dividend_yield=dividend_yield,
            logo_url=self.get_company_logo_url_from_ticker(ticker)
        )
    
    def get_all(self) -> list[Company]:
        with SessionLocal() as db:
            data = db.query(CompanyFundamental).all()
            companies = []
            for item in data:
                # Safely extract data fields using getattr to avoid SQLAlchemy issues
                item_data = getattr(item, 'data', None) or {}
                name = item_data.get("name", "") or ""
                ticker = str(getattr(item, 'company_symbol', ""))
                logo_url = item_data.get("logo_url") or self.get_company_logo_url(ticker)
                
                companies.append(Company(
                    name=name,
                    ticker=ticker,
                    logo_url=logo_url
                ))
            return companies
    
    def get_fundamental_data(self, ticker: str) -> CompanyFundamentalDto | None:
        with SessionLocal() as db:
            data = db.query(CompanyFundamental).filter(CompanyFundamental.company_symbol == ticker).first()
            if not data or data.data.get("name") == "" or data.data.get("market_cap") == 0:
                # Fetch the data from API and persist to DB
                return self.persist_fundamental_data(ticker)
            
            # Check if data is fresh (not older than 1 day)
            if data.updated_at < datetime.now(timezone.utc) - timedelta(days=1):
                # Refresh the data
                updated_company_data = self._get_fresh_company_data(ticker)
                if not updated_company_data:
                    return None
                
                if updated_company_data.name == "" or updated_company_data.market_cap == 0:
                    logger.error(f"Skip refreshing fundamental data for ticker {ticker} because it is empty")
                else:
                    # Update the row
                    data.data = updated_company_data.__dict__
                    data.updated_at = datetime.now()
                    db.commit()
                    db.refresh(data)

            return CompanyFundamentalDto(**data.data)
        
    def persist_fundamental_data(self, ticker: str) -> CompanyFundamentalDto | None:
        data = self._get_fresh_company_data(ticker)
        if not data:
            return None
        
        with SessionLocal() as db:
            try:
                # Check if record already exists
                existing_record = db.query(CompanyFundamental).filter(
                    CompanyFundamental.company_symbol == ticker
                ).first()
                
                if existing_record:
                    # Update existing record
                    logger.info(f"Updating existing fundamental data for {ticker}")
                    existing_record.data = data.__dict__
                    existing_record.updated_at = datetime.now(timezone.utc)
                    db.commit()
                    db.refresh(existing_record)
                    return CompanyFundamentalDto(**self._to_dict(existing_record).get("data"))
                else:
                    # Create new record
                    logger.info(f"Creating new fundamental data for {ticker}")
                    fundamental_data = CompanyFundamental(
                        company_symbol=ticker,
                        data=data.__dict__
                    )
                    db.add(fundamental_data)
                    db.commit()
                    db.refresh(fundamental_data)
                    return CompanyFundamentalDto(**self._to_dict(fundamental_data).get("data"))
            except Exception as e:
                logger.error(f"Error persisting fundamental data for {ticker}: {e}")
                db.rollback()
                return None

    def get_all_company_tickers(self) -> list[str]:
        with SessionLocal() as db:
            data = db.query(CompanyFundamental).all()
            return [str(item.company_symbol) for item in data if item.company_symbol is not None]

    @classmethod
    def get_company_logo_url(cls, ticker: str):
        """
        Proxy endpoint to fetch company logo and return as image response

        https://developers.brandfetch.com/dashboard/logo-api
        """
        API_KEY = os.getenv('BRAND_FETCH_API_KEY')
        params = urlencode({'c': API_KEY })
        return f"https://cdn.brandfetch.io/{ticker.upper()}/w/100/h/100?{params}"

    def get_company_logo_url_from_ticker(self, ticker: str) -> str:
        """
        Get company logo URL from ticker
        """
        company = self.get_by_ticker(ticker)
        if company:
            return company.logo_url
        
        return self.get_company_logo_url(ticker)

    def get_by_ticker(self, ticker: str) -> Company | None:
        with SessionLocal() as db:
            data = db.query(CompanyFundamental).filter(
                CompanyFundamental.company_symbol == ticker.upper()
            ).first()
            
            if not data:
                return None
            
            # Safely extract data fields using getattr to avoid SQLAlchemy issues
            item_data = getattr(data, 'data', None) or {}
            name = item_data.get("name", "") or ""
            company_ticker = str(getattr(data, 'company_symbol', ""))
            logo_url = item_data.get("logo_url") or ""
            
            return Company(
                name=name,
                ticker=company_ticker,
                logo_url=logo_url
            )
