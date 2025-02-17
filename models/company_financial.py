from sqlalchemy import Column, Integer, String, JSON, DateTime
from sqlalchemy.sql import func
from connectors.database import Base

class CompanyFinancials(Base):
    __tablename__ = "company_financials"

    id = Column(Integer, primary_key=True, index=True)
    company_symbol = Column(String, index=True)
    year = Column(Integer)
    revenue_breakdown = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
