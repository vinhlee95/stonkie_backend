"""Which trading session a page covers. Brave's date can be the crawl date, so the title and URL win over it."""

import re
from datetime import date, datetime
from urllib.parse import urlsplit

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


def session_date(
    *,
    title: str,
    url: str,
    published: datetime | None,
    period_end: date,
    month_first_numeric_dates: bool,
) -> date | None:
    """Title date, else URL date, else Brave's published date. Unparseable dates fall through to the next."""
    return (
        _title_date(title, period_end, month_first_numeric_dates)
        or _url_date(url)
        or (published.date() if published else None)
    )


def _title_date(title: str, period_end: date, month_first_numeric_dates: bool) -> date | None:
    month_day = _TITLE_MONTH_DAY_RE.search(title)
    if month_day:
        month = _MONTH_NUMBERS[month_day.group(1)[:3].lower()]
        year = period_end.year if month <= period_end.month else period_end.year - 1
        return _safe_date(year, month, int(month_day.group(2)))
    numeric = _TITLE_NUMERIC_DATE_RE.search(title) if month_first_numeric_dates else None
    return _safe_date(int(numeric.group(3)), int(numeric.group(1)), int(numeric.group(2))) if numeric else None


def _url_date(url: str) -> date | None:
    match = _URL_DATE_RE.search(urlsplit(url).path or "")
    return _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
