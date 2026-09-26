"""rename users.last_login_at to last_seen_at

Revision ID: c5e7a9b1d3f2
Revises: 7a1c2e3f4b5d
Create Date: 2026-09-26 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c5e7a9b1d3f2'
down_revision: Union[str, None] = '7a1c2e3f4b5d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('users', 'last_login_at', new_column_name='last_seen_at')


def downgrade() -> None:
    op.alter_column('users', 'last_seen_at', new_column_name='last_login_at')
