import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from connectors.database import Base
from models.user import User  # noqa: F401  (registers users table for the FK)


class PortfolioHolding(Base):
    __tablename__ = "portfolio_holdings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    ticker = Column(String, nullable=False)
    name = Column(String, nullable=True)
    shares = Column(Numeric(20, 6), nullable=False)
    # Average cost per share in the ticker's native trading currency.
    avg_cost = Column(Numeric(20, 6), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "ticker", name="uq_portfolio_holdings_user_ticker"),)
