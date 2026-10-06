from types import SimpleNamespace
from unittest.mock import patch

from services.portfolio import PortfolioService, valuation

HOLDING = SimpleNamespace(ticker="AAPL", name="Apple", shares=2.0, avg_cost=100.0, lots=())
QUOTE = {
    "close": 120.0,
    "prev_close": 110.0,
    "change_percent": 9.09,
    "currency": "USD",
    "trading_date": "2026-10-02",
    "as_of": None,
    "delayed": True,
}
QUOTES = {"AAPL": QUOTE}
META = {"AAPL": {"sector": "Technology", "country": "United States", "asset_type": "Stock"}}


class NoCalls:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected call: {name}")


def test_value_uses_passed_holdings_and_quotes_without_fetching():
    fx = SimpleNamespace(get_live_rate=lambda currency, base: 0.5)
    service = PortfolioService(portfolio=NoCalls(), yf_client=NoCalls(), fx=fx, companies=NoCalls())
    with (
        patch.object(service, "_quotes", side_effect=AssertionError("refetched quotes")),
        patch.object(service, "_holdings_metadata", return_value=META),
    ):
        result = service._value([HOLDING], QUOTES)

    assert result["summary"]["total_value"] == 2 * 120.0 * 0.5
    assert result["holdings"][0]["ticker"] == "AAPL"


def test_fx_currencies_are_major_units_of_quoted_holdings_only():
    holdings = [
        HOLDING,
        SimpleNamespace(ticker="VOD.L"),
        SimpleNamespace(ticker="NOQ"),
        SimpleNamespace(ticker="NOCCY"),
    ]
    quotes = {**QUOTES, "VOD.L": {**QUOTE, "currency": "GBp"}, "NOCCY": {**QUOTE, "currency": None}}

    assert valuation.fx_currencies(holdings, quotes) == {"USD", "GBP"}


def test_value_portfolio_leaves_holding_without_fx_rate_out_of_totals():
    vod = SimpleNamespace(ticker="VOD.L", name=None, shares=10.0, avg_cost=100.0, lots=())
    quotes = {**QUOTES, "VOD.L": {**QUOTE, "close": 200.0, "currency": "GBp"}}
    meta = {**META, "VOD.L": META["AAPL"]}

    result = valuation.value_portfolio([HOLDING, vod], quotes, meta, {"USD": 0.5, "GBP": None})

    rows = {r["ticker"]: r for r in result["holdings"]}
    assert result["summary"]["priced_count"] == 1
    assert result["summary"]["total_value"] == 2 * 120.0 * 0.5
    # Pence normalised to pounds even without an FX rate.
    assert (rows["VOD.L"]["currency"], rows["VOD.L"]["price"], rows["VOD.L"]["value"]) == ("GBP", 2.0, None)
