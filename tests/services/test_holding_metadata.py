import pytest

from services.holding_metadata import METADATA_TTL_SECONDS, get_holdings_metadata
from tests.api.test_quotes_price_changes import FakeRedis, FakeYFinanceClient


class FakeCompanies:
    def __init__(self, rows: dict[str, tuple[str, str]] | Exception):
        self.rows = rows
        self.calls: list[list[str]] = []

    def get_classifications(self, tickers):
        self.calls.append(tickers)
        if isinstance(self.rows, Exception):
            raise self.rows
        return {t: self.rows[t] for t in tickers if t in self.rows}


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("connectors.cache.redis_client", fake)
    return fake


def meta(sector, country, asset_type):
    return {"sector": sector, "country": country, "asset_type": asset_type}


def test_stored_fundamentals_used_before_yahoo_and_normalised():
    companies = FakeCompanies({"AAPL": ("TECHNOLOGY", "USA"), "JPM": ("Financial Services", "United States")})
    yf = FakeYFinanceClient({})

    result = get_holdings_metadata(["AAPL", "JPM"], yf, companies)

    assert result == {
        "AAPL": meta("Technology", "United States", "Stock"),
        "JPM": meta("Financial Services", "United States", "Stock"),
    }
    assert yf.info_calls == []


def test_falls_back_to_yahoo_info_for_etfs_and_missing_rows():
    companies = FakeCompanies({"VOO": ("", "")})
    yf = FakeYFinanceClient(
        {},
        infos={
            "VOO": {"quoteType": "ETF"},
            "NOKIA.HE": {"quoteType": "EQUITY", "sector": "Technology", "country": "Finland"},
            "BTC-USD": {"quoteType": "CRYPTOCURRENCY"},
        },
    )

    result = get_holdings_metadata(["VOO", "NOKIA.HE", "BTC-USD"], yf, companies)

    assert result == {
        "VOO": meta("Diversified", "Other", "ETF"),
        "NOKIA.HE": meta("Technology", "Finland", "Stock"),
        "BTC-USD": meta("Other", "Other", "Other"),
    }


def test_cached_for_a_week_and_served_from_cache(fake_redis):
    companies = FakeCompanies({})
    yf = FakeYFinanceClient({}, infos={"AAPL": {"quoteType": "EQUITY", "sector": "Technology", "country": "US"}})

    first = get_holdings_metadata(["AAPL"], yf, companies)
    second = get_holdings_metadata(["AAPL"], yf, companies)

    assert first == second
    assert yf.info_calls == ["AAPL"]
    assert len(companies.calls) == 1
    assert fake_redis.ttl("holding_meta:AAPL") == METADATA_TTL_SECONDS


def test_failures_answer_other_without_caching(fake_redis):
    companies = FakeCompanies(RuntimeError("db down"))
    yf = FakeYFinanceClient({}, infos={"AAPL": RuntimeError("yahoo down")})

    assert get_holdings_metadata(["AAPL"], yf, companies) == {"AAPL": meta("Other", "Other", "Other")}
    assert fake_redis.ttl("holding_meta:AAPL") == -2


def test_empty_tickers():
    companies = FakeCompanies({})
    assert get_holdings_metadata([], FakeYFinanceClient({}), companies) == {}
    assert companies.calls == []
