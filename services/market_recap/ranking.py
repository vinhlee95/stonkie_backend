from datetime import UTC
from urllib.parse import urlsplit

from services.market_recap.schemas import Candidate
from services.market_recap.source_policy import is_allowlisted


def dedupe(candidates: list[Candidate]) -> list[Candidate]:
    by_source_id: dict[str, Candidate] = {}
    for candidate in candidates:
        current = by_source_id.get(candidate.source_id)
        if current is None or candidate.score > current.score:
            by_source_id[candidate.source_id] = candidate
    return list(by_source_id.values())


def _published_timestamp(candidate: Candidate) -> float:
    published_date = candidate.published_date
    if published_date is None:
        return float("-inf")
    if published_date.tzinfo is None:
        published_date = published_date.replace(tzinfo=UTC)
    return published_date.timestamp()


_STRONG_ARTICLE_PATH_HINTS = (
    "/article/",
    "/articles/",
    "/story/",
    "/stories/",
    "/livecoverage/",
    "/news/articles/",
    "/press-releases/",
)

_WEAK_PAGE_PATH_HINTS = (
    "/hub/",
    "/video/",
    "/videos/",
    "/index",
    "/us-news",
    "/markets",
    "/market-news",
    "/apps/",
    "/releases/h15",
    "/econres",
)

_WEAK_TITLE_HINTS = (
    "live updates",
    "live coverage",
    "daily open",
    "next week",
    "stock market daily recap",
    "financial markets",
    "u.s. latest business and regional news",
    "the new york stock exchange",
    "federal reserve board - home",
    "sec.gov",
)

_INSTITUTIONAL_EVENT_TITLE_HINTS = (
    "closed board meeting",
    "board meeting",
    "selected interest rates",
    "series analyzer",
    "economic research",
)

_FI_RELEVANCE_HINTS = (
    "finland",
    "finnish",
    "helsinki",
    "omx helsinki",
    "nasdaq helsinki",
    "omxh",
)


def _quality_rank(candidate: Candidate) -> int:
    parsed = urlsplit(candidate.canonical_url)
    path = (parsed.path or "").lower()
    title = (candidate.title or "").strip().lower()
    raw_len = len(candidate.raw_content or "")

    score = 0

    # Hard-demote obvious hubs, landing pages, and utility pages.
    if path in {"", "/"}:
        score += 7
    if any(hint in path for hint in _WEAK_PAGE_PATH_HINTS):
        score += 5
    if path.endswith(".pdf"):
        score += 2
    if "/amp/" in path:
        score += 1

    # Generic roundup/live formats can be useful, but should not outrank real articles.
    if any(hint in title for hint in _WEAK_TITLE_HINTS):
        score += 4
    if "live" in title and "stock market" in title:
        score += 2
    if "recap" in title and "daily" in title:
        score += 2
    if any(hint in title for hint in _INSTITUTIONAL_EVENT_TITLE_HINTS):
        score += 5
    if title.startswith("how major us stock indexes fared"):
        score += 5

    # Reward article-shaped pages with enough substance.
    if any(hint in path for hint in _STRONG_ARTICLE_PATH_HINTS):
        score -= 3
    if any(char.isdigit() for char in path) and raw_len >= 800:
        score -= 1
    if raw_len >= 2500:
        score -= 1
    if raw_len < 400:
        score += 2

    return score


def _market_relevance_rank(candidate: Candidate, market: str) -> int:
    if market.upper() != "FI":
        return 0
    haystack = " ".join(
        [
            (candidate.title or "").lower(),
            (candidate.snippet or "").lower(),
            (candidate.raw_content or "").lower(),
            candidate.canonical_url.lower(),
        ]
    )
    return 0 if any(hint in haystack for hint in _FI_RELEVANCE_HINTS) else 6


def rank(candidates: list[Candidate], market: str = "US") -> list[Candidate]:
    return sorted(
        candidates,
        key=lambda candidate: (
            not is_allowlisted(candidate.url, market=market),
            _market_relevance_rank(candidate, market),
            _quality_rank(candidate),
            -_published_timestamp(candidate),
            -candidate.score,
            candidate.canonical_url,
        ),
    )
