from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from connectors.database import Base


class CompanyFinancialStatement(Base):
    __tablename__ = "company_financial_statement"

    id = Column(Integer, primary_key=True, index=True)
    company_symbol = Column(String, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    period_end_year = Column(Integer)
    is_ttm = Column(Boolean, default=False)
    balance_sheet = Column(JSON)
    income_statement = Column(JSON)
    cash_flow = Column(JSON)
    filing_10k_url = Column(Text)

    # Add unique constraint to prevent duplicate records for the same company and period
    __table_args__ = (UniqueConstraint("company_symbol", "period_end_year", name="uq_company_financial_symbol_year"),)
