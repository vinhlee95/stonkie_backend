from datetime import UTC, datetime, timedelta

from services.analyze_retrieval.freshness import (
    FRESHNESS_PD,
    FRESHNESS_PM,
    FRESHNESS_PW,
    FRESHNESS_PY,
    build_temporal_context_block,
    freshness_for_question,
    is_within_freshness_window,
    resolve_temporal_anchor,
)

_NOW = datetime(2026, 5, 7, 15, 0, tzinfo=UTC)  # Thursday


def test_yesterday_returns_pd() -> None:
    assert freshness_for_question("How much did Apple stock jump yesterday?") == FRESHNESS_PD


def test_last_week_and_past_week_return_pw() -> None:
    assert freshness_for_question("Apple performance last week") == FRESHNESS_PW
    assert freshness_for_question("Apple past week activity") == FRESHNESS_PW


def test_month_terms_return_pm() -> None:
    assert freshness_for_question("Apple this month") == FRESHNESS_PM
    assert freshness_for_question("Apple last month") == FRESHNESS_PM
    assert freshness_for_question("Apple past month performance") == FRESHNESS_PM


def test_year_terms_return_py() -> None:
    assert freshness_for_question("Apple this year") == FRESHNESS_PY
    assert freshness_for_question("Apple last year") == FRESHNESS_PY
    assert freshness_for_question("AAPL ytd return") == FRESHNESS_PY
    assert freshness_for_question("AAPL year to date") == FRESHNESS_PY


def test_pm_beats_py_when_both_terms_present() -> None:
    assert freshness_for_question("Apple this month vs this year") == FRESHNESS_PM


def test_pd_beats_pw_when_both_terms_present() -> None:
    assert freshness_for_question("latest jump yesterday") == FRESHNESS_PD


def test_vn_yesterday_with_diacritics_returns_pd() -> None:
    assert freshness_for_question("Cổ phiếu Apple hôm qua tăng bao nhiêu?") == FRESHNESS_PD


def test_vn_yesterday_without_diacritics_returns_pd() -> None:
    assert freshness_for_question("co phieu apple hom qua tang bao nhieu") == FRESHNESS_PD


def test_vn_other_pd_terms() -> None:
    for q in ["AAPL hôm nay", "tin sáng nay", "AAPL đêm qua", "thị trường tối qua"]:
        assert freshness_for_question(q) == FRESHNESS_PD, q


def test_vn_pw_terms() -> None:
    for q in ["AAPL tuần này", "AAPL tuần trước", "tin vừa rồi", "tin mới đây"]:
        assert freshness_for_question(q) == FRESHNESS_PW, q


def test_vn_pm_terms() -> None:
    for q in ["AAPL tháng này", "AAPL tháng trước"]:
        assert freshness_for_question(q) == FRESHNESS_PM, q


def test_vn_py_terms() -> None:
    for q in ["AAPL năm nay", "AAPL năm ngoái"]:
        assert freshness_for_question(q) == FRESHNESS_PY, q


def test_vn_signal_terms_return_pw() -> None:
    for q in ["Lợi nhuận quý 2 của Apple", "AAPL kết quả kinh doanh", "Apple tăng mạnh", "Apple lao dốc"]:
        assert freshness_for_question(q) == FRESHNESS_PW, q


def test_mixed_language_pd_wins() -> None:
    assert freshness_for_question("Apple hôm qua vs latest news") == FRESHNESS_PD


def test_factual_question_returns_none() -> None:
    assert freshness_for_question("What is Apple's revenue model?") is None


def test_empty_or_whitespace_returns_none() -> None:
    assert freshness_for_question("") is None
    assert freshness_for_question("   ") is None


def test_anchor_yesterday() -> None:
    assert resolve_temporal_anchor("How much did AAPL jump yesterday?", now=_NOW) == "yesterday = 2026-05-06"


def test_anchor_today() -> None:
    assert resolve_temporal_anchor("AAPL premarket today", now=_NOW) == "today = 2026-05-07"


def test_anchor_combined_yesterday_and_today() -> None:
    assert (
        resolve_temporal_anchor("yesterday's close vs today's open", now=_NOW)
        == "yesterday = 2026-05-06; today = 2026-05-07"
    )


def test_anchor_yesterday_crosses_month_boundary() -> None:
    now = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    assert resolve_temporal_anchor("AAPL move yesterday", now=now) == "yesterday = 2026-05-31"


def test_anchor_this_week_is_iso_mon_sun_range() -> None:
    # 2026-05-07 is a Thursday; ISO week is Mon 2026-05-04 to Sun 2026-05-10
    assert resolve_temporal_anchor("AAPL this week", now=_NOW) == "this week = 2026-05-04 to 2026-05-10"


def test_anchor_last_week_range() -> None:
    assert resolve_temporal_anchor("AAPL last week", now=_NOW) == "last week = 2026-04-27 to 2026-05-03"


def test_anchor_this_and_last_month() -> None:
    assert resolve_temporal_anchor("AAPL this month", now=_NOW) == "this month = 2026-05"
    assert resolve_temporal_anchor("AAPL last month", now=_NOW) == "last month = 2026-04"


def test_anchor_last_month_crosses_year_boundary() -> None:
    now = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
    assert resolve_temporal_anchor("AAPL last month", now=now) == "last month = 2025-12"


def test_anchor_this_and_last_year() -> None:
    assert resolve_temporal_anchor("AAPL this year", now=_NOW) == "this year = 2026"
    assert resolve_temporal_anchor("AAPL last year", now=_NOW) == "last year = 2025"


def test_anchor_vn_yesterday_with_diacritics() -> None:
    assert resolve_temporal_anchor("AAPL hôm qua", now=_NOW) == "yesterday = 2026-05-06"


def test_anchor_vn_yesterday_without_diacritics() -> None:
    assert resolve_temporal_anchor("AAPL hom qua", now=_NOW) == "yesterday = 2026-05-06"


def test_anchor_vn_week_month_year() -> None:
    assert resolve_temporal_anchor("AAPL tuần này", now=_NOW) == "this week = 2026-05-04 to 2026-05-10"
    assert resolve_temporal_anchor("AAPL tháng trước", now=_NOW) == "last month = 2026-04"
    assert resolve_temporal_anchor("AAPL năm ngoái", now=_NOW) == "last year = 2025"


def test_temporal_context_block_for_yesterday() -> None:
    block = build_temporal_context_block("How much did AAPL jump yesterday?", now=_NOW)
    assert "Date references in the question: yesterday = 2026-05-06" in block


def test_temporal_context_block_empty_when_no_anchor() -> None:
    assert build_temporal_context_block("What is Apple's revenue model?", now=_NOW) == ""


def test_window_py_admits_350_day_old_rejects_400_day_old() -> None:
    fresh = _NOW - timedelta(days=350)
    stale = _NOW - timedelta(days=400)
    assert is_within_freshness_window(fresh, policy=FRESHNESS_PY, now=_NOW) is True
    assert is_within_freshness_window(stale, policy=FRESHNESS_PY, now=_NOW) is False


def test_intraday_day_terms_return_pd() -> None:
    for q in [
        "How is AAPL doing today?",
        "AAPL after-hours move",
        "AAPL premarket activity",
        "this morning's news",
        "AAPL tonight",
        "overnight news",
        "last night's earnings",
    ]:
        assert freshness_for_question(q) == FRESHNESS_PD, q
