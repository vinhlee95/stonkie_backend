from sqlalchemy import JSON, Column, Date, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from connectors.database import Base


class TickerRecap(Base):
    __tablename__ = "ticker_recap"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, nullable=False)
    cadence = Column(String, nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    summary = Column(Text, nullable=False)
    bullets = Column(JSON, nullable=False)
    sources = Column(JSON, nullable=False)
    raw_sources = Column(JSON, nullable=True)
    price_change = Column(JSON, nullable=True)
    search_query = Column(Text, nullable=True)
    model = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("ticker", "cadence", "period_start", name="uq_ticker_recap_ticker_cadence_period_start"),
        Index(
            "ix_ticker_recap_ticker_cadence_period_start_desc",
            "ticker",
            "cadence",
            period_start.desc(),
        ),
    )
