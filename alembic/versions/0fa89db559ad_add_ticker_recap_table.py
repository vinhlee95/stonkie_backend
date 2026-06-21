"""add ticker_recap table

Revision ID: 0fa89db559ad
Revises: a65904684216
Create Date: 2026-06-21 19:17:24.761774

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0fa89db559ad"
down_revision: Union[str, None] = "a65904684216"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ticker_recap",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column("cadence", sa.String(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("bullets", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("raw_sources", sa.JSON(), nullable=True),
        sa.Column("price_change", sa.JSON(), nullable=True),
        sa.Column("search_query", sa.Text(), nullable=True),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticker", "cadence", "period_start", name="uq_ticker_recap_ticker_cadence_period_start"
        ),
    )
    op.create_index(op.f("ix_ticker_recap_id"), "ticker_recap", ["id"], unique=False)
    op.create_index(
        "ix_ticker_recap_ticker_cadence_period_start_desc",
        "ticker_recap",
        ["ticker", "cadence", sa.text("period_start DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ticker_recap_ticker_cadence_period_start_desc", table_name="ticker_recap")
    op.drop_index(op.f("ix_ticker_recap_id"), table_name="ticker_recap")
    op.drop_table("ticker_recap")
