import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from alembic import command
from alembic.config import Config
from tests.api.conftest import PROJECT_ROOT

LOTS_REVISION = "e7b2c4d6f8a1"
PREVIOUS_REVISION = "d8f1a2b3c4e5"


@pytest.fixture()
def alembic_cfg(test_engine):
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    yield cfg
    # Leave the shared test DB empty and at head for the rest of the session.
    with test_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE users CASCADE"))
    command.upgrade(cfg, "head")


def _insert_user(connection) -> str:
    user_id = str(uuid.uuid4())
    connection.execute(
        text("INSERT INTO users (id, google_sub, email) VALUES (CAST(:id AS uuid), :sub, :email)"),
        {"id": user_id, "sub": f"mig-{user_id}", "email": f"{user_id}@example.com"},
    )
    return user_id


def _insert_holding(connection, user_id: str, ticker: str, shares=None, avg_cost=None) -> str:
    holding_id = str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO portfolio_holdings (id, user_id, ticker, shares, avg_cost) "
            "VALUES (CAST(:id AS uuid), CAST(:user_id AS uuid), :ticker, :shares, :avg_cost)"
        ),
        {"id": holding_id, "user_id": user_id, "ticker": ticker, "shares": shares, "avg_cost": avg_cost},
    )
    return holding_id


def test_upgrade_backfills_one_undated_lot_per_holding(test_engine, alembic_cfg):
    command.downgrade(alembic_cfg, PREVIOUS_REVISION)
    with test_engine.begin() as connection:
        user_id = _insert_user(connection)
        holding_id = _insert_holding(connection, user_id, "AAPL", shares=10, avg_cost=150)

    command.upgrade(alembic_cfg, LOTS_REVISION)

    with test_engine.connect() as connection:
        lots = connection.execute(text("SELECT holding_id, shares, price, purchased_on FROM portfolio_lots")).all()
        nullable = connection.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'portfolio_holdings' AND column_name IN ('shares', 'avg_cost')"
            )
        ).all()
    assert [(str(h), s, p, d) for h, s, p, d in lots] == [(holding_id, Decimal("10"), Decimal("150"), None)]
    assert dict(nullable) == {"shares": "YES", "avg_cost": "YES"}


def test_downgrade_aggregates_lots_back_into_holdings(test_engine, alembic_cfg):
    with test_engine.begin() as connection:
        user_id = _insert_user(connection)
        aapl = _insert_holding(connection, user_id, "AAPL")
        for shares, price in [(10, 100), (30, 200)]:
            connection.execute(
                text("INSERT INTO portfolio_lots (holding_id, shares, price) VALUES (CAST(:h AS uuid), :s, :p)"),
                {"h": aapl, "s": shares, "p": price},
            )
        # Written by pre-lots code between migration and deploy: legacy columns set, no lots.
        _insert_holding(connection, user_id, "MSFT", shares=5, avg_cost=300)
        # No lots and no legacy numbers: can't satisfy NOT NULL, so it is dropped.
        _insert_holding(connection, user_id, "NVDA")

    command.downgrade(alembic_cfg, PREVIOUS_REVISION)

    with test_engine.connect() as connection:
        rows = connection.execute(text("SELECT ticker, shares, avg_cost FROM portfolio_holdings")).all()
    assert {t: (s, a) for t, s, a in rows} == {
        "AAPL": (Decimal("40"), Decimal("175")),
        "MSFT": (Decimal("5"), Decimal("300")),
    }
