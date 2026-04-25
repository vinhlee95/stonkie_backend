from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from services.market_recap.schemas import Bullet, Citation, RecapPayload, Source
from services.market_recap.url_utils import canonicalize_url, source_id_for


def build_source(url: str = "https://example.com/markets") -> Source:
    canonical_url = canonicalize_url(url)
    return Source(
        id=source_id_for(canonical_url),
        url=canonical_url,
        title="Markets finished higher",
        publisher="Example News",
        published_at=datetime(2026, 4, 24, 16, 30, tzinfo=UTC),
        fetched_at=datetime(2026, 4, 25, 8, 0, tzinfo=UTC),
    )


def build_payload() -> RecapPayload:
    source = build_source()
    return RecapPayload(
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        summary="US stocks rose during a busy earnings week.",
        bullets=[
            Bullet(
                text="Major indexes gained as earnings reports beat expectations.",
                citations=[Citation(source_id=source.id)],
            )
        ],
        sources=[source],
    )


class TestRecapPayload:
    def test_round_trips_through_json(self):
        payload = build_payload()

        restored = RecapPayload.model_validate_json(payload.model_dump_json())

        assert restored == payload

    def test_rejects_bullet_without_citations(self):
        with pytest.raises(ValidationError, match="List should have at least 1 item"):
            Bullet(text="Unsupported claim.", citations=[])

    def test_rejects_unknown_citation_source_id(self):
        source = build_source()

        with pytest.raises(ValidationError, match="unknown source_id"):
            RecapPayload(
                period_start=date(2026, 4, 20),
                period_end=date(2026, 4, 24),
                summary="US stocks rose during a busy earnings week.",
                bullets=[Bullet(text="Unsupported claim.", citations=[Citation(source_id="missing-source")])],
                sources=[source],
            )

    def test_accepts_multi_bullet_multi_citation_payload(self):
        first_source = build_source("https://example.com/markets?utm_source=newsletter")
        second_source = build_source("https://example.org/economy")

        payload = RecapPayload(
            period_start=date(2026, 4, 20),
            period_end=date(2026, 4, 24),
            summary="US markets rose while economic data stayed mixed.",
            bullets=[
                Bullet(
                    text="Major indexes gained.",
                    citations=[Citation(source_id=first_source.id), Citation(source_id=second_source.id)],
                ),
                Bullet(
                    text="Economic data remained mixed.",
                    citations=[Citation(source_id=second_source.id)],
                ),
            ],
            sources=[first_source, second_source],
        )

        assert payload.bullets[0].citations[0].source_id == first_source.id
