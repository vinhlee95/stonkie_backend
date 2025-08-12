"""add_unique_constraint_company_symbol_period_end_quarter

Revision ID: 812db6f0eee8
Revises: b02c543f4bf9
Create Date: 2025-08-12 08:18:41.810309

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '812db6f0eee8'
down_revision: Union[str, None] = 'b02c543f4bf9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add unique constraint on (company_symbol, period_end_quarter) to prevent duplicate records
    op.create_unique_constraint(
        'uq_company_financial_symbol_period',
        'company_quarterly_financial_statement',
        ['company_symbol', 'period_end_quarter']
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Remove the unique constraint
    op.drop_constraint(
        'uq_company_financial_symbol_period',
        'company_quarterly_financial_statement',
        type_='unique'
    )
