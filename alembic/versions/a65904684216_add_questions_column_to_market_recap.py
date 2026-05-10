"""add questions column to market_recap

Revision ID: a65904684216
Revises: e5f6a7b8c9d0
Create Date: 2026-05-10 14:45:07.679725

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a65904684216'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('market_recap', sa.Column('questions', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('market_recap', 'questions')
