from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from services.market_recap.schemas import Bullet, Citation, Source
from services.market_recap.url_utils import canonicalize_url, source_id_for
from services.ticker_recap.schemas import TickerRecapPayload


def build_source(url: str = "https://example.com/aapl") -> Source:
    canonical_url = canonicalize_url(url)
    return Source(
        id=source_id_for(canonical_url),
        url=canonical_url,
        title="Apple shares climb on strong guidance",
        publisher="Example News",
        published_at=datetime(2026, 6, 18, 16, 30, tzinfo=UTC),
        fetched_at=datetime(2026, 6, 19, 8, 0, tzinfo=UTC),
    )


def build_payload() -> TickerRecapPayload:
    source = build_source()
    return TickerRecapPayload(
        ticker="AAPL",
        cadence="daily",
        period_start=date(2026, 6, 18),
        period_end=date(2026, 6, 18),
        summary="Apple rose after upbeat guidance.",
        bullets=[
            Bullet(
                text="Apple shares climbed on strong forward guidance.",
                citations=[Citation(source_id=source.id)],
            )
        ],
        sources=[source],
    )


class TestTickerRecapPayload:
    def test_round_trips_through_json(self):
        payload = build_payload()

        restored = TickerRecapPayload.model_validate_json(payload.model_dump_json())

        assert restored == payload
        assert restored.ticker == "AAPL"
        assert restored.cadence == "daily"

    def test_rejects_unknown_citation_source_id(self):
        source = build_source()

        with pytest.raises(ValidationError, match="unknown source_id"):
            TickerRecapPayload(
                ticker="AAPL",
                cadence="daily",
                period_start=date(2026, 6, 18),
                period_end=date(2026, 6, 18),
                summary="Apple rose after upbeat guidance.",
                bullets=[Bullet(text="Unsupported claim.", citations=[Citation(source_id="missing-source")])],
                sources=[source],
            )
