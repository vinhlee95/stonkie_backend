from sqlalchemy import JSON, Column, DateTime, Integer, String, Text, UniqueConstraint
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
    filing_10q_url = Column(Text)

    # Add unique constraint to prevent duplicate records for the same company and period
    __table_args__ = (
        UniqueConstraint("company_symbol", "period_end_quarter", name="uq_company_financial_symbol_period"),
    )
