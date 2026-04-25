from datetime import UTC, date, datetime

from models.market_recap import MarketRecap
from services.market_recap.persistence import persist_recap
from services.market_recap.schemas import Bullet, Citation, RecapPayload, Source
from services.market_recap.url_utils import canonicalize_url, source_id_for


def build_payload(*, summary: str = "US stocks rose during earnings week.") -> RecapPayload:
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
        summary=summary,
        bullets=[Bullet(text="Major indexes gained.", citations=[Citation(source_id=source.id)])],
        sources=[source],
    )


def test_persist_recap_inserts_valid_row(db_session):
    payload = build_payload()

    result = persist_recap(
        db_session,
        market="US",
        cadence="weekly",
        payload=payload,
        model="phase4-test-model",
        raw_sources={"items": [{"url": payload.sources[0].url}]},
    )

    assert result.inserted is True
    assert result.replaced is False
    assert result.recap_id is not None

    restored = db_session.query(MarketRecap).filter_by(market="US", cadence="weekly").one()
    assert restored.summary == payload.summary
    assert restored.bullets == [bullet.model_dump(mode="json") for bullet in payload.bullets]
    assert restored.sources == [source.model_dump(mode="json") for source in payload.sources]
    assert restored.raw_sources == {"items": [{"url": payload.sources[0].url}]}
    assert restored.model == "phase4-test-model"


def test_persist_recap_is_idempotent_by_default(db_session):
    first_payload = build_payload(summary="first summary")
    second_payload = build_payload(summary="second summary should not overwrite")

    first = persist_recap(
        db_session,
        market="US",
        cadence="weekly",
        payload=first_payload,
        model="phase4-test-model",
        raw_sources={"run": 1},
    )
    second = persist_recap(
        db_session,
        market="US",
        cadence="weekly",
        payload=second_payload,
        model="phase4-test-model",
        raw_sources={"run": 2},
    )

    assert first.inserted is True
    assert second.inserted is False
    assert db_session.query(MarketRecap).count() == 1
    restored = db_session.query(MarketRecap).one()
    assert restored.summary == "first summary"
    assert restored.raw_sources == {"run": 1}


def test_persist_recap_replace_updates_exact_period_only(db_session):
    original = build_payload(summary="old summary")
    adjacent = RecapPayload(
        period_start=date(2026, 4, 13),
        period_end=date(2026, 4, 17),
        summary="adjacent week summary",
        bullets=original.bullets,
        sources=original.sources,
    )
    replacement = build_payload(summary="replacement summary")

    persist_recap(
        db_session,
        market="US",
        cadence="weekly",
        payload=original,
        model="phase4-test-model",
        raw_sources={"period": "target"},
    )
    persist_recap(
        db_session,
        market="US",
        cadence="weekly",
        payload=adjacent,
        model="phase4-test-model",
        raw_sources={"period": "adjacent"},
    )

    replaced = persist_recap(
        db_session,
        market="US",
        cadence="weekly",
        payload=replacement,
        model="phase4-test-model",
        raw_sources={"period": "target-updated"},
        replace=True,
    )

    assert replaced.inserted is True
    assert replaced.replaced is True
    assert db_session.query(MarketRecap).count() == 2

    target = (
        db_session.query(MarketRecap).filter_by(market="US", cadence="weekly", period_start=date(2026, 4, 20)).one()
    )
    adjacent_row = (
        db_session.query(MarketRecap).filter_by(market="US", cadence="weekly", period_start=date(2026, 4, 13)).one()
    )
    assert target.summary == "replacement summary"
    assert target.raw_sources == {"period": "target-updated"}
    assert adjacent_row.summary == "adjacent week summary"
