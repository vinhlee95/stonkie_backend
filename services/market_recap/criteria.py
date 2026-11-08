"""Per-market rules, and the facts source selection reads about each candidate (computed once per candidate)."""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlsplit

from services.market_recap.schemas import Candidate
from services.market_recap.session_dates import session_date
from services.market_recap.source_policy import is_allowlisted

MIN_STORY_CHARS = 300
DATE_GRACE = timedelta(days=1)
_OFF_TOPIC_PENALTY = 6
_BODY_ONLY_TOPIC_PENALTY = 3
_IRRELEVANT_MARKET_PENALTY = 6


@dataclass(frozen=True)
class MarketPolicy:
    # False: rank keeps the article-path bonus and selection takes the plain top of the ranked list.
    balanced_selection: bool = True
    # Words a story's title must carry to count as market coverage. None: no topic check.
    topic_re: re.Pattern[str] | None = None
    # Index names a session wrap mentions, and how many distinct ones it needs. None: no wraps.
    session_index_re: re.Pattern[str] | None = None
    session_index_min_names: int = 1
    # Phrases showing a wrap reports the week's totals (preferred for the latest session).
    week_total_re: re.Pattern[str] | None = None
    # A page must mention one of these, else it is demoted. Empty: no check.
    relevance_hints: tuple[str, ...] = ()
    # Numeric title dates read as month/day. VN titles write day/month.
    month_first_numeric_dates: bool = True


_POLICIES = {
    "US": MarketPolicy(
        topic_re=re.compile(
            r"s&p|\b(?:stocks?|shares|wall st(?:reet)?|dow|nasdaq|markets?|treasur(?:y|ies)|bonds?|yields?|fed|"
            r"federal reserve|fomc|earnings|futures|sell-?off|rally|investors?|oil prices|inflation|jobs report|"
            r"unemployment|hiring)\b",
            re.IGNORECASE,
        ),
        session_index_re=re.compile(r"s&p|\bdow\b|nasdaq", re.IGNORECASE),
        session_index_min_names=2,
        week_total_re=re.compile(r"(?:for|on) the week|weekly (?:loss|gain)", re.IGNORECASE),
    ),
    "FI": MarketPolicy(
        session_index_re=re.compile(r"omx helsinki|omxh25|helsinki 25", re.IGNORECASE),
        relevance_hints=("finland", "finnish", "helsinki", "omx helsinki", "nasdaq helsinki", "omxh"),
    ),
    # VN keeps plain ranking and its top 5: the balanced selection didn't beat it in before/after recap judging,
    # and VN outlook pieces name VN-Index too, so index names can't identify a session wrap there.
    "VN": MarketPolicy(balanced_selection=False, month_first_numeric_dates=False),
}
_DEFAULT_POLICY = MarketPolicy()


def policy_for(market: str) -> MarketPolicy:
    return _POLICIES.get(market.upper(), _DEFAULT_POLICY)


@dataclass(frozen=True)
class CandidateFacts:
    allowlisted: bool
    session_date: date | None
    content_chars: int
    is_session_wrap: bool
    reports_week_total: bool
    is_on_topic_story: bool


def candidate_facts(candidate: Candidate, *, market: str, period_end: date) -> CandidateFacts:
    policy = policy_for(market)
    title = candidate.title or ""
    raw_content = candidate.raw_content or ""
    is_article = is_article_page(candidate.canonical_url)
    return CandidateFacts(
        allowlisted=is_allowlisted(candidate.url, market=market),
        session_date=session_date(
            title=title,
            url=candidate.canonical_url,
            published=candidate.published_date,
            period_end=period_end,
            month_first_numeric_dates=policy.month_first_numeric_dates,
        ),
        content_chars=len(raw_content),
        is_session_wrap=is_article and names_enough_session_indexes(f"{title} {raw_content}", policy),
        reports_week_total=bool(policy.week_total_re and policy.week_total_re.search(raw_content)),
        is_on_topic_story=(
            is_article
            and len(raw_content) >= MIN_STORY_CHARS
            and market_relevance_rank(candidate, policy) == 0
            and topic_penalty(title, raw_content, policy) == 0
        ),
    )


def is_eligible(facts: CandidateFacts, period_start: date, period_end: date) -> bool:
    """Allowlisted, and covering a session in the period (one day of slack either side)."""
    day = facts.session_date
    return facts.allowlisted and day is not None and period_start - DATE_GRACE <= day <= period_end + DATE_GRACE


def is_article_page(canonical_url: str) -> bool:
    # Hubs, section and data pages also carry market text, but have no digits in the path (dates, article ids).
    path = (urlsplit(canonical_url).path or "").lower()
    return any(char.isdigit() for char in path) and "/video" not in path


def names_enough_session_indexes(text: str, policy: MarketPolicy) -> bool:
    if policy.session_index_re is None:
        return False
    return len({name.lower() for name in policy.session_index_re.findall(text)}) >= policy.session_index_min_names


def market_relevance_rank(candidate: Candidate, policy: MarketPolicy) -> int:
    if not policy.relevance_hints:
        return 0
    haystack = " ".join(
        [candidate.title or "", candidate.snippet or "", candidate.raw_content or "", candidate.canonical_url]
    ).lower()
    return 0 if any(hint in haystack for hint in policy.relevance_hints) else _IRRELEVANT_MARKET_PENALTY


def topic_penalty(title: str, raw_content: str, policy: MarketPolicy) -> int:
    if policy.topic_re is None or policy.topic_re.search(title):
        return 0
    return _BODY_ONLY_TOPIC_PENALTY if policy.topic_re.search(raw_content) else _OFF_TOPIC_PENALTY
