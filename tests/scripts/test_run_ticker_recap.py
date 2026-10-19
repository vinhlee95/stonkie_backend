from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from connectors.yfinance_client import TickerInfoDto
from scripts import run_ticker_recap
from scripts.run_ticker_recap import POPULAR_TICKERS, main
from services.ticker_recap.universe import HeldTicker


@pytest.fixture(autouse=True)
def no_holdings(monkeypatch):
    """Tests never read portfolio_holdings or call Yahoo unless they pass their own held_fn / lookup."""
    monkeypatch.setattr(run_ticker_recap, "_default_held", lambda: [])

    def no_yahoo():
        raise AssertionError("tests must pass lookup=")

    monkeypatch.setattr(run_ticker_recap, "_default_lookup", no_yahoo)


def recording_runner(calls):
    def runner(**kwargs):
        calls.append(kwargs)
        return {"status": "inserted"}

    return runner


def equity(ticker):
    return TickerInfoDto(name=f"{ticker} Corp", quote_type="EQUITY")


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


def test_recap_tickers_env_extends_builtin_set():
    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"status": "inserted"}

    env = {"RECAP_TICKERS": "NKE:NIKE, Inc.;DELL:Dell Technologies Inc."}
    exit_code = main(["--cadence", "daily"], runner=fake_runner, env=env)

    assert exit_code == 0
    tickers = [call["ticker"] for call in calls]
    assert tickers == list(POPULAR_TICKERS) + ["NKE", "DELL"]
    by_ticker = {call["ticker"]: call["company_name"] for call in calls}
    # Company names (incl. commas) survive parsing; market defaults to US.
    assert by_ticker["NKE"] == "NIKE, Inc."
    assert by_ticker["DELL"] == "Dell Technologies Inc."
    assert all(call["market"] == "US" for call in calls)


def test_recap_tickers_env_overrides_builtin_name_and_market():
    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"status": "inserted"}

    env = {"RECAP_TICKERS": "AAPL:Apple (override):FI"}
    exit_code = main(["--tickers", "AAPL"], runner=fake_runner, env=env)

    assert exit_code == 0
    assert calls[0]["company_name"] == "Apple (override)"
    assert calls[0]["market"] == "FI"


def test_held_tickers_are_recapped_after_popular():
    calls = []
    held = [HeldTicker("NOKIA.HE", 2)]

    assert main(["--cadence", "daily"], runner=recording_runner(calls), held_fn=lambda: held, lookup=equity) == 0

    assert [call["ticker"] for call in calls] == [*POPULAR_TICKERS, "NOKIA.HE"]
    assert (calls[-1]["market"], calls[-1]["company_name"]) == ("FI", "NOKIA.HE Corp")


def test_cli_tickers_bypass_universe():
    calls = []

    def held_fn():
        raise AssertionError("must not read holdings")

    assert main(["--tickers", "AAPL"], runner=recording_runner(calls), held_fn=held_fn) == 0
    assert [call["ticker"] for call in calls] == ["AAPL"]


def test_cli_ticker_outside_catalog_uses_its_suffix_market():
    calls = []

    assert main(["--ticker", "NOKIA.HE"], runner=recording_runner(calls)) == 0
    assert calls[0]["market"] == "FI"


def test_max_tickers_env():
    calls = []
    held = [HeldTicker(t, 1) for t in ("MSFT", "AMZN", "META")]

    main(
        ["--cadence", "daily"],
        runner=recording_runner(calls),
        env={"RECAP_MAX_TICKERS": "5"},
        held_fn=lambda: held,
        lookup=equity,
    )

    assert [call["ticker"] for call in calls] == [*POPULAR_TICKERS, "MSFT"]


def test_invalid_max_tickers_env_uses_default():
    calls = []
    held = [HeldTicker("MSFT", 1)]

    main(
        ["--cadence", "daily"],
        runner=recording_runner(calls),
        env={"RECAP_MAX_TICKERS": "abc"},
        held_fn=lambda: held,
        lookup=equity,
    )

    assert [call["ticker"] for call in calls] == [*POPULAR_TICKERS, "MSFT"]


def test_held_query_failure_falls_back_to_popular(caplog):
    calls = []

    def held_fn():
        raise RuntimeError("db down")

    assert main(["--cadence", "daily"], runner=recording_runner(calls), held_fn=held_fn, lookup=equity) == 0
    assert [call["ticker"] for call in calls] == list(POPULAR_TICKERS)
    assert "held tickers" in caplog.text


def test_overrides_run_before_held_tickers():
    calls = []
    env = {"RECAP_TICKERS": "NKE:NIKE, Inc."}

    main(
        ["--cadence", "daily"],
        runner=recording_runner(calls),
        env=env,
        held_fn=lambda: [HeldTicker("MSFT", 9)],
        lookup=equity,
    )

    assert [call["ticker"] for call in calls] == [*POPULAR_TICKERS, "NKE", "MSFT"]


def test_default_cap_fits_audio_limit():
    calls = []
    held = [HeldTicker(f"T{i}", 30 - i) for i in range(30)]

    main(["--cadence", "daily"], runner=recording_runner(calls), env={}, held_fn=lambda: held, lookup=equity)

    assert len(calls) == 20


def test_runner_exception_is_a_failure_and_run_continues(caplog):
    calls = []

    def runner(**kwargs):
        calls.append(kwargs["ticker"])
        if kwargs["ticker"] == "AAPL":
            raise RuntimeError("boom")
        return {"status": "inserted"}

    assert main(["--cadence", "daily"], runner=runner, lookup=equity) == 0
    assert calls == list(POPULAR_TICKERS)
    assert "AAPL" in caplog.text


def test_lookup_with_timeout_gives_up_on_slow_calls():
    import threading

    release = threading.Event()

    def slow(ticker):
        release.wait(5)
        return equity(ticker)

    lookup = run_ticker_recap._with_timeout(slow, timeout_s=0.05)
    try:
        assert lookup("MSFT") is None
    finally:
        release.set()
    assert run_ticker_recap._with_timeout(equity, timeout_s=1)("MSFT") == equity("MSFT")
