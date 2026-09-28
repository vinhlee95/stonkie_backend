import json

import pytest
import redis

from connectors.company import CompanyClassificationDto
from services.holding_metadata import FAILED_TTL_SECONDS, METADATA_TTL_SECONDS, get_holdings_metadata
from tests.api.test_quotes_price_changes import FakeRedis, FakeYFinanceClient


class FakeCompanies:
    def __init__(self, rows: dict[str, tuple[str, str]] | Exception):
        self.rows = rows
        self.calls: list[list[str]] = []

    def get_classifications(self, tickers):
        self.calls.append(tickers)
        if isinstance(self.rows, Exception):
            raise self.rows
        return {t: CompanyClassificationDto(*self.rows[t]) for t in tickers if t in self.rows}


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
            "QQQ": {"quoteType": "ETF", "sector": "Technology"},
            "NOKIA.HE": {"quoteType": "EQUITY", "sector": "Technology", "country": "Finland"},
            "BTC-USD": {"quoteType": "CRYPTOCURRENCY"},
        },
    )

    result = get_holdings_metadata(["VOO", "QQQ", "NOKIA.HE", "BTC-USD"], yf, companies)

    assert result == {
        "VOO": meta("Diversified", "Other", "ETF"),
        "QQQ": meta("Diversified", "Other", "ETF"),
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


def test_failures_answer_other_and_retry_after_a_short_ttl(fake_redis):
    companies = FakeCompanies(RuntimeError("db down"))
    # Yahoo sometimes answers {} instead of raising; treated as a failure too.
    yf = FakeYFinanceClient({}, infos={"AAPL": RuntimeError("yahoo down"), "MSFT": {}})

    result = get_holdings_metadata(["AAPL", "MSFT"], yf, companies)

    assert result == {"AAPL": meta("Other", "Other", "Other"), "MSFT": meta("Other", "Other", "Other")}
    assert fake_redis.ttl("holding_meta:AAPL") == FAILED_TTL_SECONDS
    assert fake_redis.ttl("holding_meta:MSFT") == FAILED_TTL_SECONDS
    assert FAILED_TTL_SECONDS < METADATA_TTL_SECONDS


def test_stored_row_without_country_falls_back_to_yahoo():
    companies = FakeCompanies({"ASML": ("TECHNOLOGY", "")})
    yf = FakeYFinanceClient(
        {}, infos={"ASML": {"quoteType": "EQUITY", "sector": "Technology", "country": "Netherlands"}}
    )

    assert get_holdings_metadata(["ASML"], yf, companies) == {"ASML": meta("Technology", "Netherlands", "Stock")}
    assert yf.info_calls == ["ASML"]


def test_only_cache_misses_reach_sources_and_partial_entries_are_refetched(fake_redis):
    fake_redis.setex("holding_meta:AAPL", 60, json.dumps(meta("Technology", "United States", "Stock")))
    fake_redis.setex("holding_meta:MSFT", 60, json.dumps({"sector": "Technology", "country": "United States"}))
    companies = FakeCompanies({"MSFT": ("Technology", "United States")})
    yf = FakeYFinanceClient({}, infos={"VOO": {"quoteType": "ETF"}})

    result = get_holdings_metadata(["AAPL", "MSFT", "VOO"], yf, companies)

    assert result == {
        "AAPL": meta("Technology", "United States", "Stock"),
        "MSFT": meta("Technology", "United States", "Stock"),
        "VOO": meta("Diversified", "Other", "ETF"),
    }
    assert companies.calls == [["MSFT", "VOO"]]
    assert yf.info_calls == ["VOO"]


def test_empty_tickers():
    companies = FakeCompanies({})
    assert get_holdings_metadata([], FakeYFinanceClient({}), companies) == {}
    assert companies.calls == []


def test_redis_failure_falls_through_to_sources(monkeypatch):
    def broken_mget(keys):
        raise redis.RedisError("down")

    monkeypatch.setattr("connectors.cache.redis_client.mget", broken_mget)
    companies = FakeCompanies({"AAPL": ("Technology", "United States")})

    assert get_holdings_metadata(["AAPL"], FakeYFinanceClient({}), companies) == {
        "AAPL": meta("Technology", "United States", "Stock")
    }
