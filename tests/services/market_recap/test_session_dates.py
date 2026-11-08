from datetime import UTC, date, datetime

import pytest

from services.market_recap.criteria import candidate_facts
from services.market_recap.schemas import Candidate
from services.market_recap.session_dates import session_date

BRAVE_DATE = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("month_first", "expected"),
    [(True, date(2026, 4, 11)), (False, date(2026, 11, 6))],  # day/month titles fall back to the Brave date
)
def test_numeric_title_date_is_read_month_first_only_when_the_market_writes_it_that_way(
    month_first: bool, expected: date
):
    assert (
        session_date(
            title="Giao dịch 4/11/2026",
            url="https://cafef.vn/giao-dich.chn",
            published=BRAVE_DATE,
            period_end=date(2026, 11, 6),
            month_first_numeric_dates=month_first,
        )
        == expected
    )


@pytest.mark.parametrize(("market", "expected"), [("US", date(2026, 4, 11)), ("VN", date(2026, 11, 6))])
def test_market_policy_sets_how_numeric_title_dates_are_read(market: str, expected: date):
    candidate = Candidate(
        title="Giao dịch 4/11/2026", url="https://cafef.vn/giao-dich.chn", published_date=BRAVE_DATE, provider="brave"
    )

    assert candidate_facts(candidate, market=market, period_end=date(2026, 11, 6)).session_date == expected
