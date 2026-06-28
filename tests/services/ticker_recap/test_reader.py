from datetime import date, datetime

from connectors.ticker_recap import TickerRecapDto
from services.ticker_recap import reader
from services.ticker_recap.reader import get_latest_recaps


def _dto(ticker: str, period_start: date) -> TickerRecapDto:
    return TickerRecapDto(
        id=1,
        ticker=ticker,
        cadence="daily",
        period_start=period_start,
        period_end=period_start,
        summary="summary",
        bullets=[],
        sources=[],
        price_change=None,
        search_query=None,
        created_at=datetime(2026, 6, 27),
    )


class _FakeConnector:
    def __init__(self, rows: list[TickerRecapDto]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, str, int]] = []

    def get_latest(self, ticker: str, cadence: str, *, limit: int = 1) -> list[TickerRecapDto]:
        self.calls.append((ticker, cadence, limit))
        return self.rows[:limit]


def test_get_latest_recaps_delegates_to_injected_connector():
    rows = [_dto("AAPL", date(2026, 6, 26))]
    fake = _FakeConnector(rows)

    result = get_latest_recaps("AAPL", "daily", limit=3, connector=fake)

    assert result == rows
    assert fake.calls == [("AAPL", "daily", 3)]


def test_get_latest_recaps_defaults_to_ticker_recap_connector(monkeypatch):
    created = []

    class _Sentinel:
        def get_latest(self, ticker, cadence, *, limit=1):
            created.append((ticker, cadence, limit))
            return []

    monkeypatch.setattr(reader, "TickerRecapConnector", _Sentinel)

    assert get_latest_recaps("TSLA", "daily", limit=1) == []
    assert created == [("TSLA", "daily", 1)]
