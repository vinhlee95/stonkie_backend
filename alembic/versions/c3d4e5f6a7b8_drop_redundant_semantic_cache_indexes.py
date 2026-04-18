"""drop redundant semantic_cache indexes

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-18 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_semantic_cache_ticker", table_name="semantic_cache")
    op.drop_index("ix_semantic_cache_expires_at", table_name="semantic_cache")


def downgrade() -> None:
    op.create_index("ix_semantic_cache_ticker", "semantic_cache", ["ticker"])
    op.create_index("ix_semantic_cache_expires_at", "semantic_cache", ["expires_at"])
