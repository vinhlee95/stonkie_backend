"""add audio columns to market_recap and ticker_recap

Revision ID: 9c13494bd82b
Revises: 0fa89db559ad
Create Date: 2026-07-18 15:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c13494bd82b'
down_revision: Union[str, None] = '0fa89db559ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AUDIO_COLUMNS = (
    ('audio_key', sa.String()),
    ('audio_duration_s', sa.Float()),
)
TABLES = ("market_recap", "ticker_recap")


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _existing_columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    """Add GCS object key + duration for the generated recap audio.

    Guarded on table/column presence: the test fixtures stamp partway through the
    migration chain and then `upgrade head`, so a given suite may have only one of
    these two tables materialized. Both tables always exist in a real database.
    """
    tables = _existing_tables()
    for table in TABLES:
        if table not in tables:
            continue
        present = _existing_columns(table)
        for name, type_ in AUDIO_COLUMNS:
            if name not in present:
                op.add_column(table, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    """Drop the audio columns."""
    tables = _existing_tables()
    for table in TABLES:
        if table not in tables:
            continue
        present = _existing_columns(table)
        for name, _ in reversed(AUDIO_COLUMNS):
            if name in present:
                op.drop_column(table, name)
