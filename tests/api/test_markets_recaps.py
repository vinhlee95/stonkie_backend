from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from connectors.database import get_db
from main import app
from models.market_recap import MarketRecap


def _seed_recap(db_session) -> None:
    recap = MarketRecap(
        market="US",
        cadence="weekly",
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        summary="Weekly recap summary",
        bullets=[{"text": "Bullet one", "citations": [{"source_id": "src-1"}]}],
        sources=[
            {
                "id": "src-1",
                "url": "https://www.reuters.com/markets/us",
                "title": "Reuters recap",
                "publisher": "reuters.com",
                "published_at": "2026-04-24T12:00:00Z",
                "fetched_at": "2026-04-25T08:00:00Z",
            }
        ],
        raw_sources={"internal_only": True},
        model="test-model",
    )
    db_session.add(recap)
    db_session.flush()
    recap.created_at = datetime(2026, 4, 25, 8, 0, tzinfo=UTC)
    db_session.commit()


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_get_market_recaps_tracer_bullet(db_session, client):
    _seed_recap(db_session)

    response = client.get("/api/markets/US/recaps?cadence=weekly&limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["market"] == "US"
    assert payload["cadence"] == "weekly"
    assert payload["latest_created_at"] == "2026-04-25T08:00:00Z"
    assert len(payload["items"]) == 1

    item = payload["items"][0]
    assert item["period_start"] == "2026-04-20"
    assert item["period_end"] == "2026-04-24"
    assert item["created_at"] == "2026-04-25T08:00:00Z"
    assert item["bullets"][0]["citations"][0]["source_id"] == item["sources"][0]["id"]


def test_get_market_recaps_orders_by_latest_period_start(db_session, client):
    _seed_recap(db_session)
    older = MarketRecap(
        market="US",
        cadence="weekly",
        period_start=date(2026, 4, 13),
        period_end=date(2026, 4, 17),
        summary="Older summary",
        bullets=[{"text": "Older bullet", "citations": [{"source_id": "src-2"}]}],
        sources=[
            {
                "id": "src-2",
                "url": "https://www.wsj.com/markets/us",
                "title": "Older source",
                "publisher": "wsj.com",
                "published_at": "2026-04-17T12:00:00Z",
                "fetched_at": "2026-04-18T08:00:00Z",
            }
        ],
        raw_sources={"internal_only": True},
        model="test-model",
    )
    db_session.add(older)
    db_session.commit()

    response = client.get("/api/markets/US/recaps?cadence=weekly&limit=2")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["period_start"] for item in items] == ["2026-04-20", "2026-04-13"]


def test_get_market_recaps_applies_limit_without_reordering(db_session, client):
    _seed_recap(db_session)
    for period_start, period_end in [(date(2026, 4, 13), date(2026, 4, 17)), (date(2026, 4, 6), date(2026, 4, 10))]:
        db_session.add(
            MarketRecap(
                market="US",
                cadence="weekly",
                period_start=period_start,
                period_end=period_end,
                summary=f"Summary {period_start}",
                bullets=[{"text": "Bullet", "citations": [{"source_id": f"src-{period_start.day}"}]}],
                sources=[
                    {
                        "id": f"src-{period_start.day}",
                        "url": f"https://example.com/{period_start.isoformat()}",
                        "title": "Source",
                        "publisher": "example.com",
                        "published_at": "2026-04-10T12:00:00Z",
                        "fetched_at": "2026-04-11T08:00:00Z",
                    }
                ],
                raw_sources={"internal_only": True},
                model="test-model",
            )
        )
    db_session.commit()

    response = client.get("/api/markets/US/recaps?cadence=weekly&limit=2")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    assert [item["period_start"] for item in items] == ["2026-04-20", "2026-04-13"]


def test_get_market_recaps_exposes_latest_created_at_for_returned_items(db_session, client):
    _seed_recap(db_session)
    first = db_session.query(MarketRecap).filter_by(period_start=date(2026, 4, 20)).one()
    first.created_at = datetime(2026, 4, 25, 7, 0, tzinfo=UTC)
    db_session.add(
        MarketRecap(
            market="US",
            cadence="weekly",
            period_start=date(2026, 4, 13),
            period_end=date(2026, 4, 17),
            summary="Second summary",
            bullets=[{"text": "B2", "citations": [{"source_id": "src-2"}]}],
            sources=[
                {
                    "id": "src-2",
                    "url": "https://example.com/2",
                    "title": "Second source",
                    "publisher": "example.com",
                    "published_at": "2026-04-17T12:00:00Z",
                    "fetched_at": "2026-04-18T08:00:00Z",
                }
            ],
            raw_sources={"internal_only": True},
            model="test-model",
            created_at=datetime(2026, 4, 25, 8, 30, tzinfo=UTC),
        )
    )
    db_session.commit()

    response = client.get("/api/markets/US/recaps?cadence=weekly&limit=2")

    assert response.status_code == 200
    payload = response.json()
    created_values = [item["created_at"] for item in payload["items"]]
    assert payload["latest_created_at"] == max(created_values)


def test_get_market_recaps_does_not_expose_raw_sources(db_session, client):
    _seed_recap(db_session)

    response = client.get("/api/markets/US/recaps?cadence=weekly&limit=1")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert "raw_sources" not in item


def test_get_market_recaps_returns_latest_available_when_current_week_missing(db_session, client):
    db_session.add(
        MarketRecap(
            market="US",
            cadence="weekly",
            period_start=date(2026, 4, 6),
            period_end=date(2026, 4, 10),
            summary="Older available summary",
            bullets=[{"text": "Older bullet", "citations": [{"source_id": "src-old"}]}],
            sources=[
                {
                    "id": "src-old",
                    "url": "https://example.com/old",
                    "title": "Older source",
                    "publisher": "example.com",
                    "published_at": "2026-04-10T12:00:00Z",
                    "fetched_at": "2026-04-11T08:00:00Z",
                }
            ],
            raw_sources={"internal_only": True},
            model="test-model",
        )
    )
    db_session.commit()

    response = client.get("/api/markets/US/recaps?cadence=weekly&limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["period_start"] == "2026-04-06"


def test_get_market_recaps_returns_empty_envelope_when_no_rows(client):
    response = client.get("/api/markets/US/recaps?cadence=weekly&limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["market"] == "US"
    assert payload["cadence"] == "weekly"
    assert payload["latest_created_at"] is None
    assert payload["items"] == []


def _seed_daily_recap(
    db_session,
    *,
    period_start: date,
    summary: str = "Daily recap summary",
    source_id: str = "src-daily-1",
    created_at: datetime | None = None,
) -> None:
    recap = MarketRecap(
        market="US",
        cadence="daily",
        period_start=period_start,
        period_end=period_start,
        summary=summary,
        bullets=[{"text": "Daily bullet", "citations": [{"source_id": source_id}]}],
        sources=[
            {
                "id": source_id,
                "url": "https://www.reuters.com/markets/us/daily",
                "title": "Reuters daily recap",
                "publisher": "reuters.com",
                "published_at": f"{period_start.isoformat()}T20:00:00Z",
                "fetched_at": f"{period_start.isoformat()}T21:00:00Z",
            }
        ],
        raw_sources={"internal_only": True},
        model="test-model",
    )
    db_session.add(recap)
    db_session.flush()
    if created_at is not None:
        recap.created_at = created_at
    db_session.commit()


def test_get_market_recaps_daily_cadence_returns_daily_row(db_session, client):
    _seed_recap(db_session)
    _seed_daily_recap(db_session, period_start=date(2026, 4, 24))

    response = client.get("/api/markets/US/recaps?cadence=daily&limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cadence"] == "daily"
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["period_start"] == "2026-04-24"
    assert item["period_end"] == "2026-04-24"
    assert item["summary"] == "Daily recap summary"


def test_get_market_recaps_daily_excludes_weekly(db_session, client):
    _seed_recap(db_session)

    response = client.get("/api/markets/US/recaps?cadence=daily&limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cadence"] == "daily"
    assert payload["items"] == []
    assert payload["latest_created_at"] is None


def test_get_market_recaps_daily_orders_by_period_start_desc(db_session, client):
    _seed_daily_recap(db_session, period_start=date(2026, 4, 22), summary="Older daily", source_id="src-daily-old")
    _seed_daily_recap(db_session, period_start=date(2026, 4, 24), summary="Newer daily", source_id="src-daily-new")

    response = client.get("/api/markets/US/recaps?cadence=daily&limit=2")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["period_start"] for item in items] == ["2026-04-24", "2026-04-22"]
    assert items[0]["summary"] == "Newer daily"
