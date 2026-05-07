from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


def _normalize(text: str) -> str:
    lowered = " ".join((text or "").lower().split())
    nfd = unicodedata.normalize("NFD", lowered)
    stripped = "".join(c for c in nfd if not unicodedata.combining(c))
    return stripped.replace("đ", "d")


_PAST_DAY_TERMS = (
    # EN
    "today",
    "yesterday",
    "tonight",
    "last night",
    "overnight",
    "premarket",
    "pre-market",
    "after hours",
    "after-hours",
    "this morning",
    # VN
    "hôm nay",
    "hôm qua",
    "đêm qua",
    "tối qua",
    "sáng nay",
)

_HIGH_RECENCY_TERMS = (
    # EN
    "latest",
    "recent",
    "this week",
    "last week",
    "past week",
    "breaking",
    "just announced",
    "newest",
    "currently happening",
    # VN
    "tuần này",
    "tuần trước",
    "tuần rồi",
    "tuần qua",
    "vừa rồi",
    "mới đây",
)

_HIGH_SIGNAL_TERMS = (
    # EN
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
    # VN
    "lợi nhuận",
    "kết quả kinh doanh",
    "doanh thu",
    "dự báo",
    "định hướng",
    "tin tức",
    "sự kiện",
    "công bố",
    "phản ứng thị trường",
    "biến động mạnh",
    "tăng mạnh",
    "giảm mạnh",
    "lao dốc",
    "bứt phá",
)

_PAST_MONTH_TERMS = (
    # EN
    "this month",
    "last month",
    "past month",
    # VN
    "tháng này",
    "tháng trước",
    "tháng rồi",
    "tháng qua",
)

_PAST_YEAR_TERMS = (
    # EN
    "this year",
    "last year",
    "past year",
    "ytd",
    "year to date",
    # VN
    "năm nay",
    "năm ngoái",
    "năm trước",
    "năm qua",
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


FRESHNESS_PD = FreshnessPolicy(value="pd", max_age=timedelta(days=2))
FRESHNESS_PW = FreshnessPolicy(value="pw", max_age=timedelta(days=7))
FRESHNESS_PM = FreshnessPolicy(value="pm", max_age=timedelta(days=31))
FRESHNESS_PY = FreshnessPolicy(value="py", max_age=timedelta(days=366))


def _matches_any(normalized_question: str, triggers: tuple[str, ...]) -> bool:
    return any(_normalize(t) in normalized_question for t in triggers)


# Tier table — ordered tightest to widest; tighter wins.
_TIER_ORDER: tuple[tuple[tuple[str, ...], FreshnessPolicy], ...] = (
    (_PAST_DAY_TERMS, FRESHNESS_PD),
    (_HIGH_RECENCY_TERMS, FRESHNESS_PW),
    (_HIGH_SIGNAL_TERMS, FRESHNESS_PW),
    (_PAST_MONTH_TERMS, FRESHNESS_PM),
    (_MEDIUM_RECENCY_TERMS, FRESHNESS_PM),
    (_PAST_YEAR_TERMS, FRESHNESS_PY),
)


def freshness_for_question(question: str) -> FreshnessPolicy | None:
    normalized = _normalize(question)
    if not normalized:
        return None
    for terms, policy in _TIER_ORDER:
        if _matches_any(normalized, terms):
            return policy
    return None


_TODAY_TRIGGERS = (
    "today",
    "this morning",
    "premarket",
    "pre-market",
    "after hours",
    "after-hours",
    "hôm nay",
    "sáng nay",
)
_YESTERDAY_TRIGGERS = (
    "yesterday",
    "last night",
    "overnight",
    "hôm qua",
    "đêm qua",
    "tối qua",
)
_THIS_WEEK_TRIGGERS = ("this week", "tuần này")
_LAST_WEEK_TRIGGERS = ("last week", "tuần trước", "tuần rồi", "tuần qua")
_THIS_MONTH_TRIGGERS = ("this month", "tháng này")
_LAST_MONTH_TRIGGERS = ("last month", "tháng trước", "tháng rồi", "tháng qua")
_THIS_YEAR_TRIGGERS = ("this year", "năm nay")
_LAST_YEAR_TRIGGERS = ("last year", "năm ngoái", "năm trước", "năm qua")


def resolve_temporal_anchor(question: str, *, now: datetime | None = None) -> str | None:
    normalized = _normalize(question)
    if not normalized:
        return None
    if now is None:
        now = datetime.now(UTC)
    today = now.date()
    yesterday = today - timedelta(days=1)

    parts: list[str] = []
    if _matches_any(normalized, _YESTERDAY_TRIGGERS):
        parts.append(f"yesterday = {yesterday.isoformat()}")
    if _matches_any(normalized, _TODAY_TRIGGERS):
        parts.append(f"today = {today.isoformat()}")

    week_monday = today - timedelta(days=today.weekday())
    if _matches_any(normalized, _THIS_WEEK_TRIGGERS):
        parts.append(f"this week = {week_monday.isoformat()} to {(week_monday + timedelta(days=6)).isoformat()}")
    if _matches_any(normalized, _LAST_WEEK_TRIGGERS):
        last_monday = week_monday - timedelta(days=7)
        parts.append(f"last week = {last_monday.isoformat()} to {(last_monday + timedelta(days=6)).isoformat()}")

    if _matches_any(normalized, _THIS_MONTH_TRIGGERS):
        parts.append(f"this month = {today.year:04d}-{today.month:02d}")
    if _matches_any(normalized, _LAST_MONTH_TRIGGERS):
        last_year = today.year if today.month > 1 else today.year - 1
        last_month = today.month - 1 if today.month > 1 else 12
        parts.append(f"last month = {last_year:04d}-{last_month:02d}")

    if _matches_any(normalized, _THIS_YEAR_TRIGGERS):
        parts.append(f"this year = {today.year}")
    if _matches_any(normalized, _LAST_YEAR_TRIGGERS):
        parts.append(f"last year = {today.year - 1}")

    return "; ".join(parts) if parts else None


def build_temporal_context_block(question: str, *, now: datetime | None = None) -> str:
    anchor = resolve_temporal_anchor(question, now=now)
    if not anchor:
        return ""
    return (
        f"\nDate references in the question: {anchor}. "
        f"Use these absolute dates when matching against source publish dates.\n"
    )


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
