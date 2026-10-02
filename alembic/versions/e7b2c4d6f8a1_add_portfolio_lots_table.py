"""add portfolio_lots table

Revision ID: e7b2c4d6f8a1
Revises: d8f1a2b3c4e5
Create Date: 2026-09-30 12:00:00.000000

Expand step: each existing holding becomes one undated lot, and the legacy holding columns turn
nullable so pre-lots code keeps working until the new code is deployed. A follow-up drops them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7b2c4d6f8a1"
down_revision: Union[str, None] = "d8f1a2b3c4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "portfolio_lots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "holding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("portfolio_holdings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("shares", sa.Numeric(20, 6), nullable=False),
        sa.Column("price", sa.Numeric(20, 6), nullable=False),
        sa.Column("purchased_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_portfolio_lots_holding_id", "portfolio_lots", ["holding_id"])
    op.execute(
        "INSERT INTO portfolio_lots (holding_id, shares, price, created_at, updated_at) "
        "SELECT id, shares, avg_cost, created_at, updated_at FROM portfolio_holdings"
    )
    op.alter_column("portfolio_holdings", "shares", nullable=True)
    op.alter_column("portfolio_holdings", "avg_cost", nullable=True)


def downgrade() -> None:
    op.execute(
        """
        UPDATE portfolio_holdings h
        SET shares = l.shares, avg_cost = l.cost / l.shares
        FROM (
            SELECT holding_id, SUM(shares) AS shares, SUM(shares * price) AS cost
            FROM portfolio_lots GROUP BY holding_id
        ) l
        WHERE l.holding_id = h.id
        """
    )
    # Holdings with neither lots nor legacy numbers can't satisfy NOT NULL.
    op.execute("DELETE FROM portfolio_holdings WHERE shares IS NULL OR avg_cost IS NULL")
    op.alter_column("portfolio_holdings", "shares", nullable=False)
    op.alter_column("portfolio_holdings", "avg_cost", nullable=False)
    op.drop_index("ix_portfolio_lots_holding_id", table_name="portfolio_lots")
    op.drop_table("portfolio_lots")
