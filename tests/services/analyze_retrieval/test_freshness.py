from datetime import UTC, datetime

from services.analyze_retrieval.freshness import (
    FRESHNESS_PD,
    FRESHNESS_PM,
    FRESHNESS_PW,
    freshness_for_question,
    resolve_temporal_anchor,
)


def test_freshness_returns_pd_for_yesterday() -> None:
    assert freshness_for_question("How much did Apple stock jump yesterday?") == FRESHNESS_PD


def test_freshness_returns_pd_for_today_and_intraday_terms() -> None:
    assert freshness_for_question("How is AAPL doing today?") == FRESHNESS_PD
    assert freshness_for_question("AAPL premarket move") == FRESHNESS_PD
    assert freshness_for_question("after-hours reaction") == FRESHNESS_PD
    assert freshness_for_question("this morning's news") == FRESHNESS_PD
    assert freshness_for_question("last night's earnings") == FRESHNESS_PD
    assert freshness_for_question("overnight news") == FRESHNESS_PD


def test_freshness_returns_pw_for_high_recency_terms() -> None:
    assert freshness_for_question("latest Apple news") == FRESHNESS_PW
    assert freshness_for_question("breaking news on AAPL") == FRESHNESS_PW
    assert freshness_for_question("recent earnings report") == FRESHNESS_PW


def test_freshness_returns_pw_for_high_signal_terms() -> None:
    assert freshness_for_question("Apple earnings reaction") == FRESHNESS_PW
    assert freshness_for_question("AAPL guidance") == FRESHNESS_PW


def test_freshness_returns_pm_for_medium_recency_terms() -> None:
    assert freshness_for_question("How is Apple doing") == FRESHNESS_PM
    assert freshness_for_question("Apple current outlook") == FRESHNESS_PM


def test_freshness_returns_none_for_general_question() -> None:
    assert freshness_for_question("What is Apple's revenue model?") is None
    assert freshness_for_question("") is None


def test_freshness_pd_takes_priority_over_pw_terms() -> None:
    # "yesterday" (PD) and "latest" (PW) both present -> PD wins
    assert freshness_for_question("latest jump yesterday") == FRESHNESS_PD


def test_resolve_temporal_anchor_yesterday() -> None:
    now = datetime(2026, 5, 7, 15, 0, tzinfo=UTC)
    assert resolve_temporal_anchor("How much did AAPL jump yesterday?", now=now) == "yesterday = 2026-05-06"


def test_resolve_temporal_anchor_today() -> None:
    now = datetime(2026, 5, 7, 15, 0, tzinfo=UTC)
    assert resolve_temporal_anchor("AAPL premarket today", now=now) == "today = 2026-05-07"


def test_resolve_temporal_anchor_combines_yesterday_and_today() -> None:
    now = datetime(2026, 5, 7, 15, 0, tzinfo=UTC)
    result = resolve_temporal_anchor("yesterday's close vs today's open", now=now)
    assert result == "yesterday = 2026-05-06; today = 2026-05-07"


def test_resolve_temporal_anchor_intraday_terms_resolve_to_today() -> None:
    now = datetime(2026, 5, 7, 15, 0, tzinfo=UTC)
    assert resolve_temporal_anchor("after-hours reaction", now=now) == "today = 2026-05-07"
    assert resolve_temporal_anchor("this morning's news", now=now) == "today = 2026-05-07"


def test_resolve_temporal_anchor_overnight_resolves_to_yesterday() -> None:
    now = datetime(2026, 5, 7, 15, 0, tzinfo=UTC)
    assert resolve_temporal_anchor("overnight news", now=now) == "yesterday = 2026-05-06"
    assert resolve_temporal_anchor("last night's earnings", now=now) == "yesterday = 2026-05-06"


def test_resolve_temporal_anchor_returns_none_when_no_match() -> None:
    now = datetime(2026, 5, 7, 15, 0, tzinfo=UTC)
    assert resolve_temporal_anchor("What is Apple's revenue model?", now=now) is None
    assert resolve_temporal_anchor("", now=now) is None


def test_resolve_temporal_anchor_handles_month_boundary() -> None:
    # yesterday crosses a month boundary
    now = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    assert resolve_temporal_anchor("AAPL move yesterday", now=now) == "yesterday = 2026-05-31"
