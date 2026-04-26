from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from scripts.run_market_recap import (
    compute_latest_completed_trading_day,
    compute_latest_completed_week,
    main,
)


def test_compute_latest_completed_week_for_saturday_and_monday_edges():
    saturday = datetime(2026, 4, 25, 12, 0)
    assert compute_latest_completed_week(now=saturday) == (date(2026, 4, 20), date(2026, 4, 24))

    monday = datetime(2026, 4, 27, 8, 0)
    assert compute_latest_completed_week(now=monday) == (date(2026, 4, 20), date(2026, 4, 24))


def test_main_accepts_explicit_period(monkeypatch):
    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"status": "inserted"}

    exit_code = main(
        [
            "--market",
            "US",
            "--period-start",
            "2026-04-20",
            "--period-end",
            "2026-04-24",
        ],
        runner=fake_runner,
    )
    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0]["period_start"] == date(2026, 4, 20)
    assert calls[0]["period_end"] == date(2026, 4, 24)


def test_main_rejects_backfill_over_max_span():
    with pytest.raises(SystemExit):
        main(
            [
                "--market",
                "US",
                "--backfill-start",
                "2026-01-01",
                "--backfill-end",
                "2026-04-24",
            ],
        )


def test_main_requires_explicit_period_for_replace():
    with pytest.raises(SystemExit):
        main(["--market", "US", "--replace"])


def test_main_returns_non_zero_on_failed_run():
    def failing_runner(**kwargs):
        return {"status": "validation_failed"}

    exit_code = main(
        [
            "--market",
            "US",
            "--period-start",
            "2026-04-20",
            "--period-end",
            "2026-04-24",
        ],
        runner=failing_runner,
    )
    assert exit_code == 1


def test_main_runs_multiple_markets_in_one_call():
    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"status": "inserted"}

    exit_code = main(
        [
            "--markets",
            "US,VN",
            "--period-start",
            "2026-04-20",
            "--period-end",
            "2026-04-24",
        ],
        runner=fake_runner,
    )
    assert exit_code == 0
    assert [call["market"] for call in calls] == ["US", "VN"]


def test_compute_latest_completed_trading_day_us_skips_weekend():
    ny = ZoneInfo("America/New_York")
    saturday = datetime(2026, 4, 25, 7, 0, tzinfo=ny)
    sunday = datetime(2026, 4, 26, 7, 0, tzinfo=ny)
    monday = datetime(2026, 4, 27, 7, 0, tzinfo=ny)
    tuesday = datetime(2026, 4, 28, 7, 0, tzinfo=ny)

    assert compute_latest_completed_trading_day("US", now=saturday) == date(2026, 4, 24)
    assert compute_latest_completed_trading_day("US", now=sunday) == date(2026, 4, 24)
    assert compute_latest_completed_trading_day("US", now=monday) == date(2026, 4, 24)
    assert compute_latest_completed_trading_day("US", now=tuesday) == date(2026, 4, 27)


def test_compute_latest_completed_trading_day_vn_uses_hcm_tz():
    hcm = ZoneInfo("Asia/Ho_Chi_Minh")
    tuesday_morning = datetime(2026, 4, 28, 7, 0, tzinfo=hcm)
    monday_morning = datetime(2026, 4, 27, 7, 0, tzinfo=hcm)

    assert compute_latest_completed_trading_day("VN", now=tuesday_morning) == date(2026, 4, 27)
    assert compute_latest_completed_trading_day("VN", now=monday_morning) == date(2026, 4, 24)


def test_compute_latest_completed_trading_day_fi_uses_helsinki_tz():
    hel = ZoneInfo("Europe/Helsinki")
    tuesday_morning = datetime(2026, 4, 28, 7, 0, tzinfo=hel)
    monday_morning = datetime(2026, 4, 27, 7, 0, tzinfo=hel)

    assert compute_latest_completed_trading_day("FI", now=tuesday_morning) == date(2026, 4, 27)
    assert compute_latest_completed_trading_day("FI", now=monday_morning) == date(2026, 4, 24)


def test_main_daily_cadence_default_runs_per_market_with_market_tz_trading_day(monkeypatch):
    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"status": "inserted"}

    fixed_now = datetime(2026, 4, 28, 7, 0)
    monkeypatch.setattr(
        "scripts.run_market_recap._now_for_market",
        lambda market: fixed_now.replace(
            tzinfo=ZoneInfo({"US": "America/New_York", "VN": "Asia/Ho_Chi_Minh", "FI": "Europe/Helsinki"}[market])
        ),
    )

    exit_code = main(
        ["--cadence", "daily", "--markets", "US,VN,FI"],
        runner=fake_runner,
    )
    assert exit_code == 0
    assert [call["market"] for call in calls] == ["US", "VN", "FI"]
    for call in calls:
        assert call["cadence"] == "daily"
        assert call["period_start"] == call["period_end"]
        assert call["period_start"] == date(2026, 4, 27)


def test_main_daily_cadence_with_explicit_period_runs_runner_once():
    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"status": "inserted"}

    exit_code = main(
        [
            "--market",
            "VN",
            "--cadence",
            "daily",
            "--period-start",
            "2026-04-23",
            "--period-end",
            "2026-04-23",
        ],
        runner=fake_runner,
    )
    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0]["cadence"] == "daily"
    assert calls[0]["period_start"] == date(2026, 4, 23)
    assert calls[0]["period_end"] == date(2026, 4, 23)
