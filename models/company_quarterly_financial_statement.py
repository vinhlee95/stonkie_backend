from sqlalchemy import Column, Integer, String, JSON, DateTime, Boolean
from sqlalchemy.sql import func
from connectors.database import Base

class CompanyQuarterlyFinancialStatement(Base):
    __tablename__ = "company_quarterly_financial_statement"

    id = Column(Integer, primary_key=True, index=True)
    company_symbol = Column(String, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    period_end_quarter = Column(String)
    balance_sheet = Column(JSON)
    income_statement = Column(JSON)
    cash_flow = Column(JSON)
