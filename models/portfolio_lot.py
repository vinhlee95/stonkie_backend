import uuid

from sqlalchemy import Column, Date, DateTime, ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from connectors.database import Base
from models.portfolio_holding import PortfolioHolding  # noqa: F401  (registers portfolio_holdings for the FK)


class PortfolioLot(Base):
    """One buy of a holding's ticker. The holding's position is the sum of its lots."""

    __tablename__ = "portfolio_lots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    holding_id = Column(
        UUID(as_uuid=True), ForeignKey("portfolio_holdings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shares = Column(Numeric(20, 6), nullable=False)
    # Price per share in the ticker's quote unit (e.g. pence for GBp), like the quote itself.
    price = Column(Numeric(20, 6), nullable=False)
    purchased_on = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
