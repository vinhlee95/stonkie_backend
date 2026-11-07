import re
from collections import Counter
from datetime import UTC, date, timedelta
from urllib.parse import urlsplit

from services.market_recap.schemas import Candidate
from services.market_recap.source_policy import is_allowlisted, registrable_domain

# VN keeps plain ranking and its top 5: the balanced selection didn't beat it in before/after recap judging,
# and VN outlook pieces name VN-Index too, so index names can't identify a session wrap there.
_PLAIN_TOP_SOURCES_MARKETS = {"VN"}
MAX_SOURCES_PER_PUBLISHER = 2
MIN_SOURCES = 5  # Below this, repeats and weaker pages are better than a recap with too little to work from.
_MIN_STORY_CHARS = 300
_DATE_GRACE = timedelta(days=1)

_TOPIC_RE_BY_MARKET = {
    "US": re.compile(
        r"s&p|\b(?:stocks?|shares|wall st(?:reet)?|dow|nasdaq|markets?|treasur(?:y|ies)|bonds?|yields?|fed|"
        r"federal reserve|fomc|earnings|futures|sell-?off|rally|investors?|oil prices|inflation|jobs report|"
        r"unemployment|hiring)\b",
        re.IGNORECASE,
    ),
}
_OFF_TOPIC_PENALTY = 6
_BODY_ONLY_TOPIC_PENALTY = 3

# (index names, how many distinct names a page must mention to count as a session wrap)
_SESSION_INDEXES_BY_MARKET = {
    "US": (re.compile(r"s&p|\bdow\b|nasdaq", re.IGNORECASE), 2),
    "FI": (re.compile(r"omx helsinki|omxh25|helsinki 25", re.IGNORECASE), 1),
}
_WEEK_TOTAL_RE_BY_MARKET = {
    "US": re.compile(r"(?:for|on) the week|weekly (?:loss|gain)", re.IGNORECASE),
}

_MONTH_NUMBERS = {
    name: number
    for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1
    )
}
_TITLE_MONTH_DAY_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
    r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(\d{1,2})\b",
    re.IGNORECASE,
)
_TITLE_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b")
_URL_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[/-]?(\d{2})[/-]?(\d{2})(?!\d)")


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


def _quality_rank(candidate: Candidate, reward_article_paths: bool = True) -> int:
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
    if reward_article_paths and any(hint in path for hint in _STRONG_ARTICLE_PATH_HINTS):
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


def _topic_penalty(candidate: Candidate, market: str) -> int:
    pattern = _TOPIC_RE_BY_MARKET.get(market.upper())
    if pattern is None:
        return 0
    if pattern.search(candidate.title or ""):
        return 0
    return _BODY_ONLY_TOPIC_PENALTY if pattern.search(candidate.raw_content or "") else _OFF_TOPIC_PENALTY


def rank(candidates: list[Candidate], market: str = "US") -> list[Candidate]:
    # No article-path bonus: those paths are one publisher's URL format, so it decided the whole top of the list.
    reward_article_paths = market.upper() in _PLAIN_TOP_SOURCES_MARKETS
    return sorted(
        candidates,
        key=lambda candidate: (
            not is_allowlisted(candidate.url, market=market),
            _market_relevance_rank(candidate, market),
            _quality_rank(candidate, reward_article_paths=reward_article_paths) + _topic_penalty(candidate, market),
            -_published_timestamp(candidate),
            -candidate.score,
            candidate.canonical_url,
        ),
    )


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _title_date(title: str, market: str, period_end: date) -> date | None:
    month_day = _TITLE_MONTH_DAY_RE.search(title)
    if month_day:
        month = _MONTH_NUMBERS[month_day.group(1)[:3].lower()]
        year = period_end.year if month <= period_end.month else period_end.year - 1
        return _safe_date(year, month, int(month_day.group(2)))
    # VN titles write day/month, so a numeric date there would be read backwards.
    numeric = _TITLE_NUMERIC_DATE_RE.search(title) if market.upper() != "VN" else None
    return _safe_date(int(numeric.group(3)), int(numeric.group(1)), int(numeric.group(2))) if numeric else None


def _session_date(candidate: Candidate, market: str, period_end: date) -> date | None:
    """The session a page covers. Brave's date can be the crawl date, so the title and URL win over it."""
    title_date = _title_date(candidate.title or "", market, period_end)
    if title_date:
        return title_date
    url_date = _URL_DATE_RE.search(urlsplit(candidate.canonical_url).path or "")
    if url_date:
        parsed = _safe_date(int(url_date.group(1)), int(url_date.group(2)), int(url_date.group(3)))
        if parsed:
            return parsed
    return candidate.published_date.date() if candidate.published_date else None


def _is_article_page(candidate: Candidate) -> bool:
    # Hubs, section and data pages also carry market text, but have no digits in the path (dates, article ids).
    path = (urlsplit(candidate.canonical_url).path or "").lower()
    return any(char.isdigit() for char in path) and "/video" not in path


def _is_session_wrap(candidate: Candidate, market: str) -> bool:
    entry = _SESSION_INDEXES_BY_MARKET.get(market.upper())
    if entry is None or not _is_article_page(candidate):
        return False
    pattern, required_names = entry
    text = f"{candidate.title or ''} {candidate.raw_content or ''}"
    return len({name.lower() for name in pattern.findall(text)}) >= required_names


def _reports_week_total(candidate: Candidate, market: str) -> bool:
    pattern = _WEEK_TOTAL_RE_BY_MARKET.get(market.upper())
    return bool(pattern and pattern.search(candidate.raw_content or ""))


def _is_on_topic_story(candidate: Candidate, market: str) -> bool:
    return (
        _is_article_page(candidate)
        and len(candidate.raw_content or "") >= _MIN_STORY_CHARS
        and _market_relevance_rank(candidate, market) == 0
        and _topic_penalty(candidate, market) == 0
    )


def _pick_session_wraps(
    candidates: list[Candidate],
    session_dates: dict[str, date | None],
    *,
    market: str,
    period_start: date,
    period_end: date,
    limit: int,
) -> list[Candidate]:
    """One wrap per trading day, oldest first. Latest day picks first: its wrap can carry the week's totals."""
    day_count = (period_end - period_start).days + 1
    days = [period_start + timedelta(days=offset) for offset in range(day_count)]
    trading_days = [day for day in days if day.weekday() < 5] or days
    publisher_counts: Counter[str] = Counter()
    wraps: list[Candidate] = []
    for day in reversed(trading_days):
        if len(wraps) >= limit:
            break
        options = [
            candidate
            for candidate in candidates
            if session_dates[candidate.source_id] == day
            and _is_session_wrap(candidate, market)
            and publisher_counts[registrable_domain(candidate.url)] < MAX_SOURCES_PER_PUBLISHER
        ]
        if not options:
            continue
        wants_week_total = day_count > 1 and not wraps
        chosen = min(
            options,
            key=lambda candidate: (
                wants_week_total and not _reports_week_total(candidate, market),
                publisher_counts[registrable_domain(candidate.url)],
                -len(candidate.raw_content or ""),
            ),
        )
        wraps.append(chosen)
        publisher_counts[registrable_domain(chosen.url)] += 1
    return list(reversed(wraps))


def select_candidates(
    ranked: list[Candidate],
    *,
    market: str,
    period_start: date,
    period_end: date,
    top_k: int,
) -> list[Candidate]:
    """Pick the sources the recap is written from: session wraps first, then on-topic stories per publisher.

    `top_k` is a ceiling, not a quota. Weak pages, stories dated outside the period and past-the-cap repeats
    are used only to reach `MIN_SOURCES`.
    """
    if market.upper() in _PLAIN_TOP_SOURCES_MARKETS:
        return ranked[: min(top_k, MIN_SOURCES)]
    session_dates = {candidate.source_id: _session_date(candidate, market, period_end) for candidate in ranked}
    eligible = [
        candidate
        for candidate in ranked
        if is_allowlisted(candidate.url, market=market)
        and (session_date := session_dates[candidate.source_id]) is not None
        and period_start - _DATE_GRACE <= session_date <= period_end + _DATE_GRACE
    ]
    selected = _pick_session_wraps(
        eligible,
        session_dates,
        market=market,
        period_start=period_start,
        period_end=period_end,
        limit=top_k,
    )
    selected_ids = {candidate.source_id for candidate in selected}
    publisher_counts = Counter(registrable_domain(candidate.url) for candidate in selected)
    for candidate in eligible:
        if len(selected) >= top_k:
            break
        publisher = registrable_domain(candidate.url)
        if (
            candidate.source_id in selected_ids
            or publisher_counts[publisher] >= MAX_SOURCES_PER_PUBLISHER
            or not _is_on_topic_story(candidate, market)
        ):
            continue
        selected.append(candidate)
        selected_ids.add(candidate.source_id)
        publisher_counts[publisher] += 1
    backfill = [candidate for candidate in ranked if candidate.source_id not in selected_ids]
    return [*selected, *backfill][: max(len(selected), min(top_k, MIN_SOURCES))]
