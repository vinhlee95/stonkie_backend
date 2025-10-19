from sqlalchemy import JSON, Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from connectors.database import Base


class CompanyFundamental(Base):
    __tablename__ = "company_fundamental"

    id = Column(Integer, primary_key=True, index=True)
    company_symbol = Column(String, index=True)
    data = Column(JSON)
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Add unique constraint to prevent duplicate records for the same company
    __table_args__ = (UniqueConstraint("company_symbol", name="uq_company_fundamental_symbol"),)
