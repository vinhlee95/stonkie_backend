"""add market_recap table

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-25 12:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_recap",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("market", sa.String(), nullable=False),
        sa.Column("cadence", sa.String(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("bullets", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("raw_sources", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("market", "cadence", "period_start", name="uq_market_recap_market_cadence_period_start"),
    )
    op.create_index(op.f("ix_market_recap_id"), "market_recap", ["id"], unique=False)
    op.create_index(
        "ix_market_recap_market_period_start_desc",
        "market_recap",
        ["market", sa.text("period_start DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_market_recap_market_period_start_desc", table_name="market_recap")
    op.drop_index(op.f("ix_market_recap_id"), table_name="market_recap")
    op.drop_table("market_recap")
