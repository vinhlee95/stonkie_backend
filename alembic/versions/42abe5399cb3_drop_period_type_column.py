"""drop period_type column

Revision ID: 42abe5399cb3
Revises: 1495f19c8162
Create Date: 2025-04-16 07:42:29.039422

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '42abe5399cb3'
down_revision: Union[str, None] = '1495f19c8162'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('company_financial_statement', 'period_type')
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('company_financial_statement', sa.Column('period_type', sa.VARCHAR(), autoincrement=False, nullable=True))
    # ### end Alembic commands ###
