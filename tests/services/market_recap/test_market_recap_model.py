from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from models.market_recap import MarketRecap
from services.market_recap.schemas import Bullet, Citation, RecapPayload, Source
from services.market_recap.url_utils import canonicalize_url, source_id_for


def build_payload() -> RecapPayload:
    canonical_url = canonicalize_url("https://example.com/markets?utm_source=newsletter")
    source = Source(
        id=source_id_for(canonical_url),
        url=canonical_url,
        title="Stocks rose this week",
        publisher="Example News",
        published_at=datetime(2026, 4, 24, 16, 30, tzinfo=UTC),
        fetched_at=datetime(2026, 4, 25, 8, 0, tzinfo=UTC),
    )
    return RecapPayload(
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        summary="US stocks rose during a busy earnings week.",
        bullets=[Bullet(text="Major indexes gained.", citations=[Citation(source_id=source.id)])],
        sources=[source],
    )


def build_recap(**overrides) -> MarketRecap:
    payload = build_payload()
    values = {
        "market": "US",
        "cadence": "weekly",
        "period_start": payload.period_start,
        "period_end": payload.period_end,
        "summary": payload.summary,
        "bullets": [bullet.model_dump(mode="json") for bullet in payload.bullets],
        "sources": [source.model_dump(mode="json") for source in payload.sources],
        "raw_sources": {"query": "weekly us market recap", "items": [{"url": payload.sources[0].url}]},
        "model": "test-model",
    }
    values.update(overrides)
    return MarketRecap(**values)


class TestMarketRecapModel:
    def test_insert_and_query_preserves_fields(self, db_session):
        recap = build_recap()

        db_session.add(recap)
        db_session.commit()

        restored = db_session.query(MarketRecap).filter_by(market="US", cadence="weekly").one()
        assert restored.period_start == date(2026, 4, 20)
        assert restored.period_end == date(2026, 4, 24)
        assert restored.summary == "US stocks rose during a busy earnings week."
        assert restored.model == "test-model"

    def test_duplicate_market_cadence_period_start_raises_integrity_error(self, db_session):
        db_session.add(build_recap())
        db_session.commit()

        db_session.add(build_recap(summary="Duplicate recap."))

        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_json_columns_round_trip_nested_data(self, db_session):
        recap = build_recap()

        db_session.add(recap)
        db_session.commit()

        restored = db_session.query(MarketRecap).filter_by(market="US", cadence="weekly").one()
        assert restored.bullets == [
            {
                "text": "Major indexes gained.",
                "citations": [{"source_id": restored.sources[0]["id"]}],
            }
        ]
        assert restored.sources[0]["url"] == "https://example.com/markets"
        assert restored.raw_sources == {
            "query": "weekly us market recap",
            "items": [{"url": "https://example.com/markets"}],
        }
