from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_HIGH_RECENCY_TERMS = (
    "latest",
    "today",
    "recent",
    "this week",
    "breaking",
    "just announced",
    "newest",
    "currently happening",
)

_HIGH_SIGNAL_TERMS = (
    "earnings",
    "guidance",
    "news",
    "event",
    "events",
    "announced",
    "announcement",
    "stock reaction",
    "market reaction",
    "market moving",
    "selloff",
    "rally",
)

_MEDIUM_RECENCY_TERMS = (
    "current",
    "currently",
    "now",
    "outlook",
    "sentiment",
    "how is",
    "how are",
    "performance",
    "doing",
    "trend",
)


@dataclass(frozen=True)
class FreshnessPolicy:
    value: str
    max_age: timedelta


FRESHNESS_PW = FreshnessPolicy(value="pw", max_age=timedelta(days=7))
FRESHNESS_PM = FreshnessPolicy(value="pm", max_age=timedelta(days=31))


def freshness_for_question(question: str) -> FreshnessPolicy | None:
    normalized = " ".join((question or "").lower().split())
    if not normalized:
        return None

    if any(term in normalized for term in _HIGH_RECENCY_TERMS):
        return FRESHNESS_PW
    if any(term in normalized for term in _HIGH_SIGNAL_TERMS):
        return FRESHNESS_PW
    if any(term in normalized for term in _MEDIUM_RECENCY_TERMS):
        return FRESHNESS_PM
    return None


def is_within_freshness_window(
    published_at: datetime | None,
    *,
    policy: FreshnessPolicy | None,
    now: datetime | None = None,
) -> bool:
    if policy is None or published_at is None:
        return True

    if now is None:
        now = datetime.now(UTC)

    normalized = published_at.astimezone(UTC) if published_at.tzinfo else published_at.replace(tzinfo=UTC)
    return normalized >= now - policy.max_age
