from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.sql import func

from connectors.database import Base


class CompanyInsight(Base):
    __tablename__ = "company_insights"

    id = Column(Integer, primary_key=True, index=True)
    company_symbol = Column(String, index=True)
    slug = Column(String, index=True)
    insight_type = Column(String)
    title = Column(String)
    content = Column(String)
    thumbnail_url = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
