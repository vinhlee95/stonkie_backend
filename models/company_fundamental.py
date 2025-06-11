from sqlalchemy import Column, Integer, String, JSON, DateTime
from sqlalchemy.sql import func
from connectors.database import Base

class CompanyFundamental(Base):
    __tablename__ = "company_fundamental"

    id = Column(Integer, primary_key=True, index=True)
    company_symbol = Column(String, index=True)
    data = Column(JSON)
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
