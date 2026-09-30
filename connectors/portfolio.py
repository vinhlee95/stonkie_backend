import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import delete, exists, func, select, text, update
from sqlalchemy.orm import Session

from connectors.database import SessionLocal
from models.portfolio_holding import PortfolioHolding
from models.portfolio_lot import PortfolioLot

LOT_FIELDS = ("shares", "price", "purchased_on")


@dataclass(frozen=True)
class LotDto:
    id: uuid.UUID
    ticker: str
    shares: float
    price: float
    purchased_on: date | None
    created_at: datetime


@dataclass(frozen=True)
class HoldingDto:
    ticker: str
    name: str | None
    # Sum of the lots' shares.
    shares: float
    # Share-weighted average lot price, in the ticker's quote unit.
    avg_cost: float
    # Newest purchase first; undated lots last.
    lots: tuple[LotDto, ...]
    created_at: datetime
    updated_at: datetime


def _to_lot_dto(row: PortfolioLot, ticker: str) -> LotDto:
    return LotDto(
        id=row.id,
        ticker=ticker,
        shares=float(row.shares),
        price=float(row.price),
        purchased_on=row.purchased_on,
        created_at=row.created_at,
    )


def _to_holding_dto(row: PortfolioHolding, lots: list[PortfolioLot]) -> HoldingDto:
    shares = sum((lot.shares for lot in lots), Decimal(0))
    cost = sum((lot.shares * lot.price for lot in lots), Decimal(0))
    return HoldingDto(
        ticker=row.ticker,
        name=row.name,
        shares=float(shares),
        avg_cost=float(cost / shares),
        lots=tuple(_to_lot_dto(lot, row.ticker) for lot in lots),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class HoldingLimitExceeded(Exception):
    pass


class LotLimitExceeded(Exception):
    pass


def _lock_user(db: Session, user_id) -> None:
    """Serialise a user's portfolio writes so cap checks and last-lot cleanup can't race."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:user_id))"), {"user_id": str(user_id)})


def _sync_legacy_position(db: Session, holding_id: uuid.UUID) -> None:
    """Mirror the lots' aggregate into the legacy holding columns, which pre-lots code (deploy overlap,
    rollback) still reads. Drop together with those columns in the contract migration."""
    shares, cost = db.execute(
        select(func.sum(PortfolioLot.shares), func.sum(PortfolioLot.shares * PortfolioLot.price)).where(
            PortfolioLot.holding_id == holding_id
        )
    ).one()
    if shares:
        db.execute(
            update(PortfolioHolding)
            .where(PortfolioHolding.id == holding_id)
            .values(shares=shares, avg_cost=cost / shares)
        )


class PortfolioConnector:
    def list_holdings(self, user_id) -> list[HoldingDto]:
        """Holdings with their lots, by ticker. A holding without lots holds nothing and is skipped."""
        with SessionLocal() as db:
            rows = db.execute(
                select(PortfolioHolding, PortfolioLot)
                .join(PortfolioLot, PortfolioLot.holding_id == PortfolioHolding.id)
                .where(PortfolioHolding.user_id == user_id)
                .order_by(
                    PortfolioHolding.ticker,
                    PortfolioLot.purchased_on.desc().nulls_last(),
                    PortfolioLot.created_at.desc(),
                )
            ).all()
            grouped: dict[uuid.UUID, tuple[PortfolioHolding, list[PortfolioLot]]] = {}
            for holding, lot in rows:
                grouped.setdefault(holding.id, (holding, []))[1].append(lot)
            return [_to_holding_dto(holding, lots) for holding, lots in grouped.values()]

    def add_lot(
        self,
        *,
        user_id,
        ticker: str,
        name: str | None,
        shares: float,
        price: float,
        purchased_on: date | None,
        max_holdings: int | None = None,
        max_lots: int | None = None,
    ) -> LotDto:
        """Add a buy lot, creating the ticker's holding with its first lot. Raises HoldingLimitExceeded
        or LotLimitExceeded when a cap would be exceeded; checks and insert share one transaction."""
        with SessionLocal() as db:
            _lock_user(db, user_id)
            holding = db.execute(
                select(PortfolioHolding).where(PortfolioHolding.user_id == user_id, PortfolioHolding.ticker == ticker)
            ).scalar_one_or_none()
            if holding is None:
                held = db.execute(
                    select(func.count()).select_from(PortfolioHolding).where(PortfolioHolding.user_id == user_id)
                ).scalar_one()
                if max_holdings is not None and held >= max_holdings:
                    raise HoldingLimitExceeded(ticker)
                holding = PortfolioHolding(user_id=user_id, ticker=ticker, name=name)
                db.add(holding)
                db.flush()
            else:
                lots = db.execute(
                    select(func.count()).select_from(PortfolioLot).where(PortfolioLot.holding_id == holding.id)
                ).scalar_one()
                if max_lots is not None and lots >= max_lots:
                    raise LotLimitExceeded(ticker)
                # An omitted name keeps the stored one.
                if name is not None:
                    holding.name = name
            lot = PortfolioLot(holding_id=holding.id, shares=shares, price=price, purchased_on=purchased_on)
            db.add(lot)
            db.flush()
            _sync_legacy_position(db, holding.id)
            db.refresh(lot)
            dto = _to_lot_dto(lot, ticker)
            db.commit()
        return dto

    def update_lot(self, *, user_id, lot_id: uuid.UUID, changes: Mapping[str, object]) -> LotDto | None:
        """Apply `changes` (any of shares, price, purchased_on; purchased_on=None clears the date) to one
        of the user's lots. None when the lot doesn't exist or belongs to someone else."""
        with SessionLocal() as db:
            row = db.execute(
                select(PortfolioLot, PortfolioHolding.ticker)
                .join(PortfolioHolding, PortfolioHolding.id == PortfolioLot.holding_id)
                .where(PortfolioLot.id == lot_id, PortfolioHolding.user_id == user_id)
            ).one_or_none()
            if row is None:
                return None
            lot, ticker = row
            for field in LOT_FIELDS:
                if field in changes:
                    setattr(lot, field, changes[field])
            db.flush()
            _sync_legacy_position(db, lot.holding_id)
            db.refresh(lot)
            dto = _to_lot_dto(lot, ticker)
            db.commit()
        return dto

    def delete_lot(self, *, user_id, lot_id: uuid.UUID) -> bool:
        """Delete one of the user's lots; the holding goes too once its last lot is gone."""
        with SessionLocal() as db:
            _lock_user(db, user_id)
            holding_id = db.execute(
                select(PortfolioLot.holding_id)
                .join(PortfolioHolding, PortfolioHolding.id == PortfolioLot.holding_id)
                .where(PortfolioLot.id == lot_id, PortfolioHolding.user_id == user_id)
            ).scalar_one_or_none()
            if holding_id is None:
                return False
            db.execute(delete(PortfolioLot).where(PortfolioLot.id == lot_id))
            _sync_legacy_position(db, holding_id)
            db.execute(
                delete(PortfolioHolding).where(
                    PortfolioHolding.id == holding_id,
                    ~exists().where(PortfolioLot.holding_id == holding_id),
                )
            )
            db.commit()
        return True

    def delete_holding(self, *, user_id, ticker: str) -> bool:
        """Delete a position; its lots go with it (ON DELETE CASCADE)."""
        with SessionLocal() as db:
            result = db.execute(
                delete(PortfolioHolding).where(PortfolioHolding.user_id == user_id, PortfolioHolding.ticker == ticker)
            )
            db.commit()
            return result.rowcount > 0
