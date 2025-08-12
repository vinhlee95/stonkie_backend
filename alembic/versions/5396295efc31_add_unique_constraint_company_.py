"""add_unique_constraint_company_fundamental_symbol

Revision ID: 5396295efc31
Revises: 812db6f0eee8
Create Date: 2025-08-12 08:27:36.175578

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5396295efc31'
down_revision: Union[str, None] = '812db6f0eee8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add unique constraint on company_symbol to prevent duplicate records
    # Note: This constraint was already added manually to the database
    op.create_unique_constraint(
        'uq_company_fundamental_symbol',
        'company_fundamental',
        ['company_symbol']
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Remove the unique constraint
    op.drop_constraint(
        'uq_company_fundamental_symbol',
        'company_fundamental',
        type_='unique'
    )
