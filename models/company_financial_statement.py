from sqlalchemy import Column, Integer, String, JSON, DateTime, Boolean
from sqlalchemy.sql import func
from connectors.database import Base

class CompanyFinancialStatement(Base):
    __tablename__ = "company_financial_statement"

    id = Column(Integer, primary_key=True, index=True)
    company_symbol = Column(String, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    period_end_year = Column(Integer)
    is_ttm =  Column(Boolean, default=False)
    period_type = Column(String) # annual, quarterly
    balance_sheet = Column(JSON)
    income_statement = Column(JSON)
    cash_flow = Column(JSON)
