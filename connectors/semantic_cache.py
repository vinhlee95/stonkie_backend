import logging
import re
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from sqlalchemy import delete
from sqlalchemy.sql import func

from ai_models.openai import OpenAIModel
from connectors.database import SessionLocal
from models.semantic_cache import SemanticCacheEntry

logger = logging.getLogger(__name__)


class TTLTier(StrEnum):
    HISTORICAL = "historical"  # 7 days
    RECENT = "recent"  # 24 hours
    MARKET = "market"  # 1 hour


TTL_DURATIONS = {
    TTLTier.HISTORICAL: timedelta(days=7),
    TTLTier.RECENT: timedelta(hours=24),
    TTLTier.MARKET: timedelta(hours=1),
}

# Year patterns: 2023, FY2023, FY 2023
# Quarter+year: Q3 2023, Q1 2024, Q3'23
# Explicit historical: "last year", "fiscal year", "past 3 years"
HISTORICAL_PATTERNS = re.compile(
    r"\b("
    r"(?:fy\s*)?20\d{2}"
    r"|q[1-4]\s*['']?\s*\d{2,4}"
    r"|last\s+year"
    r"|fiscal\s+year"
    r"|past\s+\d+\s+years?"
    r"|over\s+the\s+last\s+\d+\s+years?"
    r")\b",
    re.IGNORECASE,
)

MARKET_PATTERNS = re.compile(
    r"\b("
    r"stock\s+price|share\s+price|current\s+price"
    r"|market\s+cap|trading\s+volume"
    r"|today|right\s+now"
    r")\b",
    re.IGNORECASE,
)


def normalize_question(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def detect_ttl_tier(question: str) -> TTLTier:
    normalized = normalize_question(question)
    if MARKET_PATTERNS.search(normalized):
        return TTLTier.MARKET
    if HISTORICAL_PATTERNS.search(normalized):
        return TTLTier.HISTORICAL
    return TTLTier.RECENT


class SemanticCache:
    def __init__(self):
        self.openai_model = OpenAIModel()

    def embed(self, text: str) -> list[float]:
        return self.openai_model.generate_embedding(text)

    def store(
        self,
        ticker: str,
        question: str,
        answer: str,
        sources: dict | list | None,
        model_used: str,
        embedding: list[float],
        related_questions: list[str] | None = None,
        ttl_seconds: int | None = None,
    ) -> SemanticCacheEntry:
        tier = detect_ttl_tier(question)
        now = datetime.now(timezone.utc)
        expires_at = now + (timedelta(seconds=ttl_seconds) if ttl_seconds is not None else TTL_DURATIONS[tier])

        entry = SemanticCacheEntry(
            ticker=ticker.upper(),
            question_text=question,
            question_embedding=embedding,
            answer_text=answer,
            sources=sources,
            related_questions=related_questions if related_questions else None,
            model_used=model_used,
            ttl_tier=tier,
            expires_at=expires_at,
        )

        with SessionLocal() as db:
            db.add(entry)
            db.commit()
            db.refresh(entry)
            logger.info("Cached response for %s (tier=%s, expires=%s)", ticker, tier, expires_at)
            return entry

    def lookup(self, ticker: str, embedding: list[float], threshold: float = 0.20) -> SemanticCacheEntry | None:
        distance = SemanticCacheEntry.question_embedding.cosine_distance(embedding)

        with SessionLocal() as db:
            row = (
                db.query(SemanticCacheEntry, distance.label("distance"))
                .filter(
                    SemanticCacheEntry.ticker == ticker.upper(),
                    SemanticCacheEntry.expires_at > func.now(),
                )
                .order_by(distance)
                .first()
            )

            if row is None or row.distance > threshold:
                return None

            entry = row.SemanticCacheEntry
            logger.info(
                "Cache hit for %s (distance=%.4f, question=%s)",
                ticker,
                row.distance,
                entry.question_text[:60],
            )
            return entry

    def invalidate_ticker(self, ticker: str) -> int:
        with SessionLocal() as db:
            result = db.execute(delete(SemanticCacheEntry).where(SemanticCacheEntry.ticker == ticker.upper()))
            db.commit()
            count = result.rowcount
            logger.info("Invalidated %d cached entries for %s", count, ticker)
            return count
