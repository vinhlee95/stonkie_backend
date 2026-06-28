from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from connectors import ticker_recap as ticker_recap_connector
from main import app
from models.ticker_recap import TickerRecap


@pytest.fixture()
def client(test_engine, monkeypatch):
    # Bind the connector's session to the test DB (phase-7 pattern); the read
    # endpoint goes through TickerRecapConnector, not Depends(get_db).
    monkeypatch.setattr(
        ticker_recap_connector,
        "SessionLocal",
        sessionmaker(bind=test_engine, autocommit=False, autoflush=False),
    )
    with TestClient(app) as test_client:
        yield test_client


def _seed_recap(
    db_session,
    *,
    ticker: str = "AAPL",
    cadence: str = "daily",
    period_start: date = date(2026, 6, 26),
    summary: str = "Apple recap summary",
    source_id: str = "src-1",
    created_at: datetime | None = None,
) -> None:
    recap = TickerRecap(
        ticker=ticker,
        cadence=cadence,
        period_start=period_start,
        period_end=period_start,
        summary=summary,
        bullets=[{"text": "Apple bullet", "citations": [{"source_id": source_id}]}],
        sources=[
            {
                "id": source_id,
                "url": "https://www.reuters.com/markets/companies/aapl",
                "title": "Reuters Apple recap",
                "publisher": "reuters.com",
                "published_at": f"{period_start.isoformat()}T20:00:00Z",
                "fetched_at": f"{period_start.isoformat()}T21:00:00Z",
            }
        ],
        raw_sources={"internal_only": True},
        price_change={"change_percent": 3.14, "close": 210.0, "trading_date": period_start.isoformat()},
        search_query="why did Apple stock rise",
        model="test-model",
    )
    db_session.add(recap)
    db_session.flush()
    if created_at is not None:
        recap.created_at = created_at
    db_session.commit()


def test_get_company_recaps_tracer_bullet(db_session, client):
    _seed_recap(db_session, created_at=datetime(2026, 6, 27, 5, 0, tzinfo=UTC))

    response = client.get("/api/companies/AAPL/recaps?cadence=daily&limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ticker"] == "AAPL"
    assert payload["cadence"] == "daily"
    assert payload["latest_created_at"] == "2026-06-27T05:00:00Z"
    assert len(payload["items"]) == 1

    item = payload["items"][0]
    assert item["period_start"] == "2026-06-26"
    assert item["period_end"] == "2026-06-26"
    assert item["created_at"] == "2026-06-27T05:00:00Z"
    assert item["summary"] == "Apple recap summary"
    assert item["bullets"][0]["citations"][0]["source_id"] == item["sources"][0]["id"]
    assert item["price_change"]["change_percent"] == 3.14
    # raw_sources + questions are NOT exposed
    assert "raw_sources" not in item
    assert "questions" not in item


def test_get_company_recaps_limit_orders_by_period_start_desc(db_session, client):
    _seed_recap(db_session, period_start=date(2026, 6, 24), summary="Older", source_id="src-old")
    _seed_recap(db_session, period_start=date(2026, 6, 26), summary="Newer", source_id="src-new")

    response = client.get("/api/companies/AAPL/recaps?cadence=daily&limit=2")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["period_start"] for item in items] == ["2026-06-26", "2026-06-24"]
    assert items[0]["summary"] == "Newer"


def test_get_company_recaps_limit_caps_items(db_session, client):
    for day in (24, 25, 26):
        _seed_recap(db_session, period_start=date(2026, 6, day), source_id=f"src-{day}")

    response = client.get("/api/companies/AAPL/recaps?cadence=daily&limit=1")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1


def test_get_company_recaps_unknown_ticker_returns_empty_envelope(client):
    response = client.get("/api/companies/ZZZZ/recaps?cadence=daily&limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ticker"] == "ZZZZ"
    assert payload["cadence"] == "daily"
    assert payload["latest_created_at"] is None
    assert payload["items"] == []


def test_get_company_recaps_cadence_isolates_rows(db_session, client):
    _seed_recap(db_session, cadence="daily", summary="Daily one")

    response = client.get("/api/companies/AAPL/recaps?cadence=weekly&limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cadence"] == "weekly"
    assert payload["items"] == []


def test_get_company_recaps_ticker_is_case_insensitive(db_session, client):
    _seed_recap(db_session, ticker="AAPL")

    response = client.get("/api/companies/aapl/recaps?cadence=daily&limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ticker"] == "AAPL"
    assert len(payload["items"]) == 1
