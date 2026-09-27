from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from connectors.yfinance_client import LiveQuoteDto, parse_live_quote

NY_TZ = ZoneInfo("America/New_York")
# Fri 2026-09-25 16:00 New York
MARKET_TIME = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)


def hourly_bars(closes_by_day: dict[str, list[float]], tz: ZoneInfo = NY_TZ) -> pd.DataFrame:
    index, closes = [], []
    for day, day_closes in closes_by_day.items():
        for hour, close in enumerate(day_closes):
            index.append(pd.Timestamp(f"{day} {10 + hour}:30", tz=str(tz)))
            closes.append(close)
    return pd.DataFrame({"Close": closes}, index=pd.DatetimeIndex(index))


BARS = hourly_bars({"2026-09-23": [100.0, 101.0], "2026-09-24": [102.0, 103.0], "2026-09-25": [104.0, 105.0]})


def meta(**overrides) -> dict:
    base = {
        "regularMarketPrice": 105.5,
        "regularMarketTime": int(MARKET_TIME.timestamp()),
        "previousClose": 103.2,
        "currency": "USD",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def test_parses_live_price_time_and_official_previous_close():
    assert parse_live_quote(meta(), BARS) == LiveQuoteDto(
        price=105.5,
        prev_close=103.2,
        currency="USD",
        market_time=MARKET_TIME,
        trading_date=date(2026, 9, 25),
    )


def test_previous_close_falls_back_to_prior_day_last_bar():
    assert parse_live_quote(meta(previousClose=None), BARS).prev_close == 103.0


# 01:00 UTC Saturday: still Friday evening in New York, already Saturday in Tokyo.
LATE = int(datetime(2026, 9, 26, 1, 0, tzinfo=UTC).timestamp())


def test_trading_date_uses_exchange_timezone_from_meta():
    quote = parse_live_quote(meta(regularMarketTime=LATE, exchangeTimezoneName="Asia/Tokyo"), BARS)
    assert quote.trading_date == date(2026, 9, 26)


def test_trading_date_falls_back_to_bars_timezone():
    quote = parse_live_quote(meta(regularMarketTime=LATE), BARS)
    assert quote.trading_date == date(2026, 9, 25)


def test_trading_date_falls_back_to_utc_without_timezone_or_bars():
    quote = parse_live_quote(meta(regularMarketTime=LATE), pd.DataFrame({"Close": []}))
    assert quote.trading_date == date(2026, 9, 26)


def test_accepts_datetime_market_time():
    assert parse_live_quote(meta(regularMarketTime=MARKET_TIME), BARS).market_time == MARKET_TIME


def test_missing_or_nan_price_returns_none():
    assert parse_live_quote(meta(regularMarketPrice=None), BARS) is None
    assert parse_live_quote(meta(regularMarketPrice=float("nan")), BARS) is None


def test_missing_market_time_returns_none():
    assert parse_live_quote(meta(regularMarketTime=None), BARS) is None


def test_no_prior_day_and_no_previous_close_returns_none():
    single_day = hourly_bars({"2026-09-25": [104.0, 105.0]})
    assert parse_live_quote(meta(previousClose=None), single_day) is None


def test_non_positive_previous_close_returns_none():
    assert parse_live_quote(meta(previousClose=0), hourly_bars({"2026-09-25": [105.0]})) is None
