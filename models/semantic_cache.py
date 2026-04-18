from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from connectors.database import Base


class SemanticCacheEntry(Base):
    __tablename__ = "semantic_cache"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    question_text = Column(Text, nullable=False)
    question_embedding = Column(Vector(1536), nullable=False)
    answer_text = Column(Text, nullable=False)
    sources = Column(JSONB)
    model_used = Column(String)
    ttl_tier = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
