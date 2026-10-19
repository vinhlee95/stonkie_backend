from dataclasses import dataclass
from datetime import UTC, date, datetime

from connectors.ticker_recap import TickerRecapDto
from services.portfolio import PortfolioService, recaps

TODAY = date(2026, 10, 19)


def recap(ticker: str, period_end: date, created_at: datetime | None = datetime(2026, 10, 17, 3, 10, tzinfo=UTC)):
    return TickerRecapDto(
        id=hash(ticker) % 1000,
        ticker=ticker,
        cadence="daily",
        period_start=period_end,
        period_end=period_end,
        summary=f"{ticker} recap",
        bullets=[{"text": "B", "citations": []}],
        sources=[],
        price_change=None,
        search_query=None,
        created_at=created_at,
    )


@dataclass
class Holding:
    ticker: str


class FakePortfolio:
    def __init__(self, tickers):
        self.tickers = tickers

    def list_holdings(self, user_id):
        return [Holding(t) for t in self.tickers]


class FakeRecaps:
    def __init__(self, latest):
        self.latest = latest
        self.calls = []

    def get_latest_many(self, tickers, cadence):
        self.calls.append((tickers, cadence))
        return {t: r for t, r in self.latest.items() if t in tickers}


def service(tickers, latest) -> tuple[PortfolioService, FakeRecaps]:
    fake = FakeRecaps(latest)
    return (
        PortfolioService(
            portfolio=FakePortfolio(tickers), yf_client=object(), fx=object(), companies=object(), recaps=fake
        ),
        fake,
    )


def test_is_fresh_boundary():
    assert recaps.is_fresh(recap("A", date(2026, 10, 15)), TODAY)
    assert not recaps.is_fresh(recap("A", date(2026, 10, 14)), TODAY)


def test_to_json_without_created_at():
    body = recaps.to_json("A", recap("A", TODAY, created_at=None))
    assert body["recap"]["created_at"] is None
    assert body["recap"]["period_end"] == "2026-10-19"


def test_to_json_created_at_is_utc_z():
    assert recaps.to_json("A", recap("A", TODAY))["recap"]["created_at"] == "2026-10-17T03:10:00Z"


def test_get_recaps_holding_order_fresh_only():
    svc, fake = service(
        ["NOKIA.HE", "AAPL", "MSFT", "OLD"],
        {
            "AAPL": recap("AAPL", date(2026, 10, 16)),
            "NOKIA.HE": recap("NOKIA.HE", date(2026, 10, 15)),
            "OLD": recap("OLD", date(2026, 10, 14)),
            "NVDA": recap("NVDA", TODAY),
        },
    )

    body = svc.get_recaps("user-1", today=TODAY)

    assert [item["ticker"] for item in body] == ["NOKIA.HE", "AAPL"]
    assert fake.calls == [(["NOKIA.HE", "AAPL", "MSFT", "OLD"], "daily")]


def test_get_recaps_no_holdings():
    svc, _ = service([], {})
    assert svc.get_recaps("user-1", today=TODAY) == []


def test_get_recaps_defaults_today_to_utc_clock(monkeypatch):
    from services.portfolio import service as portfolio_service

    monkeypatch.setattr(portfolio_service, "_utcnow", lambda: datetime(2026, 10, 20, 0, 5, tzinfo=UTC))
    svc, _ = service(["AAPL"], {"AAPL": recap("AAPL", date(2026, 10, 15))})

    assert svc.get_recaps("user-1") == []
