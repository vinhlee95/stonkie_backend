from sqlalchemy import JSON, Column, Date, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from connectors.database import Base


class MarketRecap(Base):
    __tablename__ = "market_recap"

    id = Column(Integer, primary_key=True, index=True)
    market = Column(String, nullable=False)
    cadence = Column(String, nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    summary = Column(Text, nullable=False)
    bullets = Column(JSON, nullable=False)
    sources = Column(JSON, nullable=False)
    raw_sources = Column(JSON, nullable=True)
    model = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("market", "cadence", "period_start", name="uq_market_recap_market_cadence_period_start"),
        Index("ix_market_recap_market_period_start_desc", "market", period_start.desc()),
    )
