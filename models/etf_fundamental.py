from sqlalchemy import JSON, Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from connectors.database import Base


class ETFFundamental(Base):
    __tablename__ = "etf_fundamental"

    id = Column(Integer, primary_key=True, index=True)
    isin = Column(String, index=True)
    ticker = Column(String, index=True, nullable=True)
    fund_provider = Column(String, index=True)

    # Core metadata as JSON (all non-array fields except fund_provider)
    core_metadata = Column(JSON)  # {name, fund_size_billions, ter_percent, replication_method, ...}

    # Separate JSON columns for each array type
    holdings = Column(JSON)  # [{name, weight_percent}, ...]
    sector_allocation = Column(JSON)  # [{sector, weight_percent}, ...]
    country_allocation = Column(JSON)  # [{country, weight_percent}, ...]

    source_url = Column(String, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("isin", name="uq_etf_fundamental_isin"),)
