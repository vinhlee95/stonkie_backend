from datetime import UTC, date, datetime

from services.ticker_recap.schemas import Bullet, Citation, Source, TickerRecapPayload

PERIOD_START = date(2026, 6, 22)
PERIOD_END = date(2026, 6, 26)


def build_payload(
    *,
    ticker: str = "AAPL",
    summary: str = "AAPL rose on strong iPhone demand.",
    period_start: date = PERIOD_START,
    period_end: date = PERIOD_END,
) -> TickerRecapPayload:
    source = Source(
        id="src-1",
        url="https://www.reuters.com/markets/aapl",
        title="Apple rallies",
        publisher="Reuters",
        published_at=datetime(2026, 6, 25, 16, 30, tzinfo=UTC),
        fetched_at=datetime(2026, 6, 26, 8, 0, tzinfo=UTC),
    )
    return TickerRecapPayload(
        ticker=ticker,
        cadence="daily",
        period_start=period_start,
        period_end=period_end,
        summary=summary,
        bullets=[Bullet(text="Apple gained on demand.", citations=[Citation(source_id=source.id)])],
        sources=[source],
    )


def test_upsert_inserts_new_recap(recap_connector):
    result = recap_connector.upsert_recap(
        ticker="AAPL",
        cadence="daily",
        payload=build_payload(),
        model="phase7-test-model",
    )

    assert result.inserted is True
    assert result.replaced is False
    assert result.recap_id is not None

    latest = recap_connector.get_latest("AAPL", "daily")
    assert len(latest) == 1
    assert latest[0].summary == "AAPL rose on strong iPhone demand."


def test_upsert_is_idempotent_by_default(recap_connector):
    first = recap_connector.upsert_recap(
        ticker="AAPL", cadence="daily", payload=build_payload(summary="first summary"), model="m"
    )
    second = recap_connector.upsert_recap(
        ticker="AAPL", cadence="daily", payload=build_payload(summary="second should not overwrite"), model="m"
    )

    assert first.inserted is True
    assert second.inserted is False
    assert second.recap_id == first.recap_id

    latest = recap_connector.get_latest("AAPL", "daily", limit=10)
    assert len(latest) == 1
    assert latest[0].summary == "first summary"


def test_upsert_replace_updates_exact_period_only(recap_connector):
    recap_connector.upsert_recap(
        ticker="AAPL", cadence="daily", payload=build_payload(summary="old summary"), model="m"
    )
    recap_connector.upsert_recap(
        ticker="AAPL",
        cadence="daily",
        payload=build_payload(summary="adjacent day", period_start=date(2026, 6, 19), period_end=date(2026, 6, 19)),
        model="m",
    )

    replaced = recap_connector.upsert_recap(
        ticker="AAPL",
        cadence="daily",
        payload=build_payload(summary="replacement summary"),
        model="m",
        replace=True,
    )

    assert replaced.inserted is True
    assert replaced.replaced is True

    latest = recap_connector.get_latest("AAPL", "daily", limit=10)
    assert len(latest) == 2
    by_period = {dto.period_start: dto.summary for dto in latest}
    assert by_period[PERIOD_START] == "replacement summary"
    assert by_period[date(2026, 6, 19)] == "adjacent day"


def test_upsert_stores_price_change_and_search_query(recap_connector):
    price_change = {"change_percent": -1.64, "close": 192.53, "prev_close": 195.74, "trading_date": "2026-06-26"}
    recap_connector.upsert_recap(
        ticker="NVDA",
        cadence="daily",
        payload=build_payload(ticker="NVDA"),
        model="m",
        price_change=price_change,
        search_query="why did NVDA fall today",
    )

    dto = recap_connector.get_latest("NVDA", "daily")[0]
    assert dto.price_change == price_change
    assert dto.search_query == "why did NVDA fall today"


def test_upsert_round_trips_bullets_and_sources(recap_connector):
    payload = build_payload()
    recap_connector.upsert_recap(ticker="AAPL", cadence="daily", payload=payload, model="m")

    dto = recap_connector.get_latest("AAPL", "daily")[0]
    assert dto.bullets == [bullet.model_dump(mode="json") for bullet in payload.bullets]
    assert dto.sources == [source.model_dump(mode="json") for source in payload.sources]


def test_get_latest_unknown_ticker_returns_empty(recap_connector):
    assert recap_connector.get_latest("ZZZZ", "daily") == []
