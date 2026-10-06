from types import SimpleNamespace
from unittest.mock import patch

from services.portfolio import valuation as portfolio_service

HOLDING = SimpleNamespace(ticker="AAPL", name="Apple", shares=2.0, avg_cost=100.0, lots=())
QUOTES = {
    "AAPL": {
        "close": 120.0,
        "prev_close": 110.0,
        "change_percent": 9.09,
        "currency": "USD",
        "trading_date": "2026-10-02",
        "as_of": None,
        "delayed": True,
    }
}


class NoCalls:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected call: {name}")


def test_get_portfolio_uses_passed_holdings_and_quotes_without_fetching():
    fx = SimpleNamespace(get_live_rate=lambda currency, base: 0.5)
    meta = {"AAPL": {"sector": "Technology", "country": "United States", "asset_type": "Stock"}}
    with (
        patch.object(portfolio_service, "get_quotes", side_effect=AssertionError("refetched quotes")),
        patch.object(portfolio_service, "get_holdings_metadata", return_value=meta),
    ):
        result = portfolio_service.get_portfolio(
            "user-1", NoCalls(), NoCalls(), fx=fx, companies=NoCalls(), quotes=QUOTES, holdings=[HOLDING]
        )

    assert result["summary"]["total_value"] == 2 * 120.0 * 0.5
    assert result["holdings"][0]["ticker"] == "AAPL"
