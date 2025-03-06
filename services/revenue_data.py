from pydantic import BaseModel
from typing import Literal
from itertools import chain
from connectors.database import SessionLocal
from logging import getLogger
from models.company_financial import CompanyFinancials

logger = getLogger(__name__)

class ProductRevenueBreakdown(BaseModel):
    product: str
    revenue: int
    percentage: float

class RegionRevenueBreakdown(BaseModel):
    region: str
    revenue: int
    percentage: float

class RevenueBreakdown(BaseModel):
    type: Literal["product"]
    breakdown: list[ProductRevenueBreakdown]

class RegionBreakdown(BaseModel):
    type: Literal["region"]
    breakdown: list[RegionRevenueBreakdown]


class RevenueBreakdownDTO(BaseModel):
    year: int
    revenue_breakdown: list[RevenueBreakdown | RegionBreakdown]

class NewRevenueBreakdownDTO(BaseModel):
    year: int
    product_breakdown: list[ProductRevenueBreakdown]
    region_breakdown: list[RegionRevenueBreakdown]

def get_revenue_breakdown_for_company(ticker: str) -> list[NewRevenueBreakdownDTO] | None:
    """Get revenue breakdown for a given company"""
    db = SessionLocal()
    try:
        financial_data = db.query(CompanyFinancials).filter(CompanyFinancials.company_symbol == ticker.upper()).order_by(CompanyFinancials.year.desc())
        if financial_data.count() == 0:
            return None

        revenue_breakdown: list[NewRevenueBreakdownDTO] = []

        for data in financial_data.all():
            year = data.year
            product_breakdown = list(chain.from_iterable([item.get('breakdown') for item in data.revenue_breakdown if item.get('type') == "product"]))
            region_breakdown = list(chain.from_iterable([item.get('breakdown') for item in data.revenue_breakdown if item.get('type') == "region"]))
            
            revenue_breakdown.append(NewRevenueBreakdownDTO(
                year=year,
                product_breakdown=[ProductRevenueBreakdown(**item) for item in product_breakdown],
                region_breakdown=[RegionRevenueBreakdown(**item) for item in region_breakdown]
            ))
        
        return revenue_breakdown
    except Exception as e:
        logger.error(f"Error getting revenue breakdown for company", {
            "ticker": ticker,
            "error": str(e)
        })
        return None
