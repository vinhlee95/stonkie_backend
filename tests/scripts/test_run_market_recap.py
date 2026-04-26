from datetime import date, datetime

import pytest

from scripts.run_market_recap import compute_latest_completed_week, main


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


def test_main_accepts_daily_cadence_as_noop_without_runner_calls():
    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"status": "inserted"}

    exit_code = main(["--market", "VN", "--cadence", "daily"], runner=fake_runner)
    assert exit_code == 0
    assert calls == []
