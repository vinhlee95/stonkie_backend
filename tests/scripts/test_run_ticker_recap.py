from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from scripts.run_ticker_recap import POPULAR_TICKERS, main


def test_tracer_all_tickers_succeed_returns_zero():
    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"status": "inserted"}

    exit_code = main(["--cadence", "daily"], runner=fake_runner)

    assert exit_code == 0
    assert [call["ticker"] for call in calls] == list(POPULAR_TICKERS)


def test_all_tickers_fail_returns_one():
    def failing_runner(**kwargs):
        return {"status": "skipped_no_results"}

    assert main(["--cadence", "daily"], runner=failing_runner) == 1


def test_partial_success_returns_zero():
    statuses = {
        "NVDA": {"status": "inserted"},
        "AAPL": {"status": "skipped_no_results"},
        "TSLA": {"status": "skipped_no_price"},
        "GOOG": {"status": "generation_failed"},
    }

    def fake_runner(**kwargs):
        return statuses[kwargs["ticker"]]

    assert main(["--cadence", "daily"], runner=fake_runner) == 0


def test_computes_latest_completed_us_trading_day(monkeypatch):
    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"status": "inserted"}

    # Tue 05:00 Helsinki == Mon 22:00 ET -> Monday is the latest completed US day.
    tuesday_5am_helsinki = datetime(2026, 4, 28, 5, 0, tzinfo=ZoneInfo("Europe/Helsinki"))
    monkeypatch.setattr(
        "scripts.run_ticker_recap._now_for_market",
        lambda market: tuesday_5am_helsinki,
    )

    assert main(["--tickers", "NVDA"], runner=fake_runner) == 0
    assert calls[0]["period_start"] == date(2026, 4, 27)
    assert calls[0]["period_end"] == date(2026, 4, 27)


def test_explicit_period_overrides_computed_day():
    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"status": "inserted"}

    exit_code = main(
        ["--ticker", "AAPL", "--period-start", "2026-06-26", "--period-end", "2026-06-26"],
        runner=fake_runner,
    )

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0]["period_start"] == date(2026, 6, 26)
    assert calls[0]["period_end"] == date(2026, 6, 26)


def test_replace_requires_explicit_period():
    with pytest.raises(SystemExit):
        main(["--ticker", "AAPL", "--replace"])
