"""add semantic_cache table with pgvector

Revision ID: a1b2c3d4e5f6
Revises: f76057e3b7e5
Create Date: 2026-04-18 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f76057e3b7e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "semantic_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(), nullable=True),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("question_embedding", Vector(1536), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("sources", JSONB(), nullable=True),
        sa.Column("model_used", sa.String(), nullable=True),
        sa.Column("ttl_tier", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_index("ix_semantic_cache_ticker", "semantic_cache", ["ticker"])
    op.create_index("ix_semantic_cache_expires_at", "semantic_cache", ["expires_at"])
    op.create_index(
        "ix_semantic_cache_ticker_expires",
        "semantic_cache",
        ["ticker", "expires_at"],
    )
    op.execute(
        "CREATE INDEX ix_semantic_cache_embedding_hnsw "
        "ON semantic_cache USING hnsw (question_embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.drop_table("semantic_cache")
    op.execute("DROP EXTENSION IF EXISTS vector")
