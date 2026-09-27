from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert

from connectors.database import SessionLocal
from models.portfolio_holding import PortfolioHolding


@dataclass(frozen=True)
class HoldingDto:
    ticker: str
    name: str | None
    shares: float
    avg_cost: float
    created_at: datetime
    updated_at: datetime


def _to_dto(row: PortfolioHolding) -> HoldingDto:
    return HoldingDto(
        ticker=row.ticker,
        name=row.name,
        shares=float(row.shares),
        avg_cost=float(row.avg_cost),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class HoldingLimitExceeded(Exception):
    pass


class PortfolioConnector:
    def list_holdings(self, user_id: str) -> list[HoldingDto]:
        with SessionLocal() as db:
            rows = db.execute(
                select(PortfolioHolding).where(PortfolioHolding.user_id == user_id).order_by(PortfolioHolding.ticker)
            ).scalars()
            return [_to_dto(row) for row in rows]

    def upsert_holding(
        self,
        *,
        user_id: str,
        ticker: str,
        name: str | None,
        shares: float,
        avg_cost: float,
        max_holdings: int | None = None,
    ) -> HoldingDto:
        """Insert or replace a holding. Raises HoldingLimitExceeded when inserting a new
        ticker would exceed max_holdings; the check and insert share one transaction."""
        stmt = insert(PortfolioHolding).values(
            user_id=user_id, ticker=ticker, name=name, shares=shares, avg_cost=avg_cost
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_portfolio_holdings_user_ticker",
            set_={
                # An omitted name keeps the stored one.
                "name": func.coalesce(stmt.excluded.name, PortfolioHolding.name),
                "shares": shares,
                "avg_cost": avg_cost,
                "updated_at": func.now(),
            },
        ).returning(PortfolioHolding)
        with SessionLocal() as db:
            if max_holdings is not None:
                # Serialise writes per user so concurrent PUTs can't both pass the count check.
                db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:user_id))"), {"user_id": str(user_id)})
                tickers = set(
                    db.execute(select(PortfolioHolding.ticker).where(PortfolioHolding.user_id == user_id)).scalars()
                )
                if ticker not in tickers and len(tickers) >= max_holdings:
                    raise HoldingLimitExceeded(ticker)
            row = db.execute(select(PortfolioHolding).from_statement(stmt)).scalar_one()
            dto = _to_dto(row)
            db.commit()
        return dto

    def delete_holding(self, *, user_id: str, ticker: str) -> bool:
        with SessionLocal() as db:
            result = db.execute(
                delete(PortfolioHolding).where(PortfolioHolding.user_id == user_id, PortfolioHolding.ticker == ticker)
            )
            db.commit()
            return result.rowcount > 0
