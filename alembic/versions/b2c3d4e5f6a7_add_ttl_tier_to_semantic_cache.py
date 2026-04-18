"""add ttl_tier column to semantic_cache

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-18 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "semantic_cache",
        sa.Column("ttl_tier", sa.String(), nullable=False, server_default="recent"),
    )
    op.alter_column("semantic_cache", "ttl_tier", server_default=None)


def downgrade() -> None:
    op.drop_column("semantic_cache", "ttl_tier")
