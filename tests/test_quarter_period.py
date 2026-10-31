from datetime import date

from core.quarter_period import calendar_quarter, parse_quarter_end, parse_quarter_label, sort_quarters_newest_first


def test_parse_quarter_end_handles_single_digit_month():
    assert parse_quarter_end("6/30/2026") == date(2026, 6, 30)
    assert parse_quarter_end("12/31/2025") == date(2025, 12, 31)


def test_parse_quarter_end_returns_none_for_bad_values():
    assert parse_quarter_end(None) is None
    assert parse_quarter_end("") is None
    assert parse_quarter_end("TTM") is None
    assert parse_quarter_end("2024-Q3") is None


def test_sort_quarters_newest_first_uses_date_not_string_order():
    # Lexically "9/30/2025" > "6/30/2026" > "12/31/2025"; by date the order is reversed.
    quarters = ["9/30/2025", "9/30/2024", "6/30/2026", "12/31/2025", "3/31/2026"]

    assert sort_quarters_newest_first(quarters) == ["6/30/2026", "3/31/2026", "12/31/2025", "9/30/2025", "9/30/2024"]


def test_sort_quarters_newest_first_puts_unparseable_last():
    assert sort_quarters_newest_first(["TTM", "6/30/2026", None, "9/30/2025"])[:2] == ["6/30/2026", "9/30/2025"]


def test_parse_quarter_label():
    assert parse_quarter_label("2026-Q3") == (2026, 3)
    assert parse_quarter_label("2025-q1") == (2025, 1)
    assert parse_quarter_label("latest") is None
    assert parse_quarter_label("2026-Q5") is None
    assert parse_quarter_label("6/30/2026") is None
    assert parse_quarter_label(None) is None


def test_calendar_quarter():
    assert calendar_quarter("3/31/2026") == (2026, 1)
    assert calendar_quarter("4/30/2026") == (2026, 2)
    assert calendar_quarter("6/30/2026") == (2026, 2)
    assert calendar_quarter("12/31/2025") == (2025, 4)
    assert calendar_quarter("TTM") is None
