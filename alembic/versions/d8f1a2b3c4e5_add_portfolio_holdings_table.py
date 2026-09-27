"""add portfolio_holdings table

Revision ID: d8f1a2b3c4e5
Revises: c5e7a9b1d3f2
Create Date: 2026-09-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd8f1a2b3c4e5'
down_revision: Union[str, None] = 'c5e7a9b1d3f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'portfolio_holdings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ticker', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('shares', sa.Numeric(20, 6), nullable=False),
        sa.Column('avg_cost', sa.Numeric(20, 6), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('user_id', 'ticker', name='uq_portfolio_holdings_user_ticker'),
    )
    op.create_index('ix_portfolio_holdings_user_id', 'portfolio_holdings', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_portfolio_holdings_user_id', table_name='portfolio_holdings')
    op.drop_table('portfolio_holdings')
