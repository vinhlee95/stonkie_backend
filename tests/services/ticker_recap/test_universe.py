from connectors.yfinance_client import TickerInfoDto as TickerInfo
from services.ticker_recap.universe import HeldTicker, build_universe, market_for_ticker

POPULAR = {
    "NVDA": {"name": "NVIDIA Corporation", "market": "US"},
    "AAPL": {"name": "Apple Inc.", "market": "US"},
    "TSLA": {"name": "Tesla, Inc.", "market": "US"},
    "GOOG": {"name": "Alphabet Inc.", "market": "US"},
}


def equity(ticker: str) -> TickerInfo:
    return TickerInfo(name=f"{ticker} Corp", quote_type="EQUITY")


def test_market_for_ticker_plain_is_us():
    assert market_for_ticker("AAPL") == "US"
    assert market_for_ticker("BRK-B") == "US"


def test_market_for_ticker_suffixes():
    assert market_for_ticker("NOKIA.HE") == "FI"
    assert market_for_ticker("nokia.he") == "FI"
    assert market_for_ticker("VNM.VN") == "VN"
    assert market_for_ticker("SAP.DE") is None
    assert market_for_ticker("RR.L") is None


def test_build_universe_runs_popular_then_overrides_then_held_by_holders():
    held = [HeldTicker("NOKIA.HE", 5), HeldTicker("MSFT", 3)]
    overrides = {"NKE": {"name": "NIKE", "market": "US"}}
    catalog, _ = build_universe(POPULAR, held, overrides, lookup=equity, cap=100)
    assert list(catalog) == [*POPULAR, "NKE", "NOKIA.HE", "MSFT"]
    assert catalog["NOKIA.HE"] == {"name": "NOKIA.HE Corp", "market": "FI"}


def test_build_universe_name_comes_from_lookup_not_holdings():
    catalog, _ = build_universe(
        {}, [HeldTicker("MSFT", 1)], {}, lookup=lambda t: TickerInfo("Microsoft", "EQUITY"), cap=100
    )
    assert catalog["MSFT"]["name"] == "Microsoft"


def test_build_universe_lookup_without_name_uses_ticker():
    catalog, _ = build_universe({}, [HeldTicker("MSFT", 1)], {}, lookup=lambda t: TickerInfo(None, "EQUITY"), cap=100)
    assert catalog["MSFT"]["name"] == "MSFT"


def test_build_universe_failed_lookup_still_tries_ticker():
    catalog, _ = build_universe({}, [HeldTicker("MSFT", 1)], {}, lookup=lambda t: None, cap=100)
    assert catalog["MSFT"] == {"name": "MSFT", "market": "US"}


def test_build_universe_drops_unsupported_and_non_equities():
    held = [HeldTicker(t, 1) for t in ("SAP.DE", "VOO", "BTC-USD", "^GSPC", "MSFT")]
    types = {"VOO": "ETF", "BTC-USD": "CRYPTOCURRENCY", "^GSPC": "INDEX", "MSFT": "EQUITY"}
    catalog, stats = build_universe({}, held, {}, lookup=lambda t: TickerInfo(t, types[t]), cap=100)
    assert list(catalog) == ["MSFT"]
    assert stats["dropped_market"] == 1
    assert stats["dropped_type"] == 3


def test_build_universe_popular_already_listed_not_duplicated_or_looked_up():
    looked_up = []

    def lookup(ticker):
        looked_up.append(ticker)
        return equity(ticker)

    catalog, _ = build_universe(POPULAR, [HeldTicker("AAPL", 4), HeldTicker("MSFT", 1)], {}, lookup=lookup, cap=100)
    assert list(catalog).count("AAPL") == 1
    assert catalog["AAPL"] == POPULAR["AAPL"]
    assert looked_up == ["MSFT"]


def test_build_universe_cap_cuts_held_only_and_stops_lookups():
    looked_up = []

    def lookup(ticker):
        looked_up.append(ticker)
        return equity(ticker)

    held = [HeldTicker(f"T{i}", 10 - i) for i in range(5)]
    overrides = {"NKE": {"name": "NIKE", "market": "US"}}
    catalog, stats = build_universe(POPULAR, held, overrides, lookup=lookup, cap=6)
    assert list(catalog) == [*POPULAR, "NKE", "T0"]
    assert looked_up == ["T0"]
    assert stats["capped"] == 4


def test_build_universe_cap_skips_dropped_tickers():
    held = [HeldTicker("VOO", 3), HeldTicker("MSFT", 2), HeldTicker("AMZN", 1)]
    types = {"VOO": "ETF", "MSFT": "EQUITY", "AMZN": "EQUITY"}
    catalog, _ = build_universe({}, held, {}, lookup=lambda t: TickerInfo(t, types[t]), cap=1)
    assert list(catalog) == ["MSFT"]


def test_build_universe_overrides_never_filtered():
    overrides = {"SAP.DE": {"name": "SAP", "market": "US"}}
    catalog, _ = build_universe({}, [], overrides, lookup=lambda t: None, cap=0)
    assert catalog == overrides


def test_build_universe_stats_counts():
    held = [HeldTicker("MSFT", 2), HeldTicker("AAPL", 1)]
    _, stats = build_universe(POPULAR, held, {}, lookup=equity, cap=100)
    assert stats == {"popular": 4, "held": 1, "dropped_market": 0, "dropped_type": 0, "capped": 0}
