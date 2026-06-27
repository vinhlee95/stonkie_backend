from datetime import UTC, date, datetime

from services.ticker_recap.schemas import Bullet, Citation, Source, TickerRecapPayload

PERIOD_START = date(2026, 6, 22)
PERIOD_END = date(2026, 6, 26)


def _source(
    *,
    source_id: str,
    url: str = "https://www.reuters.com/markets/one",
    published_at: datetime,
) -> Source:
    return Source(
        id=source_id,
        url=url,
        title=source_id,
        publisher="publisher",
        published_at=published_at,
        fetched_at=published_at,
    )


def _in_window_source(source_id: str) -> Source:
    return _source(source_id=source_id, published_at=datetime(2026, 6, 25, 10, 0, tzinfo=UTC))


def _payload(
    *, bullets: list[Bullet], sources: list[Source], summary: str = "AAPL rose on strong demand."
) -> TickerRecapPayload:
    return TickerRecapPayload(
        ticker="AAPL",
        cadence="daily",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        summary=summary,
        bullets=bullets,
        sources=sources,
    )


def _well_formed() -> TickerRecapPayload:
    sources = [_in_window_source(f"src-{i}") for i in range(1, 4)]
    bullets = [Bullet(text=f"b{i}", citations=[Citation(source_id=f"src-{i}")]) for i in range(1, 4)]
    return _payload(bullets=bullets, sources=sources)


def test_well_formed_payload_passes():
    from services.ticker_recap.validator import validate_recap

    result = validate_recap(_well_formed(), period_start=PERIOD_START, period_end=PERIOD_END, ticker="AAPL")
    assert result.ok is True
    assert result.failures == []


def test_empty_summary_fails():
    from services.ticker_recap.validator import REASON_EMPTY_SUMMARY, validate_recap

    payload = _payload(
        bullets=[Bullet(text=f"b{i}", citations=[Citation(source_id=f"src-{i}")]) for i in range(1, 4)],
        sources=[_in_window_source(f"src-{i}") for i in range(1, 4)],
        summary="   ",
    )
    result = validate_recap(payload, period_start=PERIOD_START, period_end=PERIOD_END, ticker="AAPL")
    assert result.ok is False
    assert REASON_EMPTY_SUMMARY in result.failures


def test_too_few_bullets_fails():
    from services.ticker_recap.validator import REASON_BULLET_COUNT, validate_recap

    payload = _payload(
        bullets=[Bullet(text=f"b{i}", citations=[Citation(source_id=f"src-{i}")]) for i in range(1, 3)],
        sources=[_in_window_source(f"src-{i}") for i in range(1, 3)],
    )
    result = validate_recap(payload, period_start=PERIOD_START, period_end=PERIOD_END, ticker="AAPL")
    assert result.ok is False
    assert REASON_BULLET_COUNT in result.failures


def test_bullet_with_no_citation_fails():
    from services.ticker_recap.validator import REASON_BULLET_MISSING_CITATION, validate_recap

    # Bullet.citations has min_length=1; model_construct bypasses the schema guard
    # to prove the validator independently rejects a citation-less bullet before persist.
    uncited = Bullet.model_construct(text="b3", citations=[])
    bullets = [
        Bullet(text="b1", citations=[Citation(source_id="src-1")]),
        Bullet(text="b2", citations=[Citation(source_id="src-2")]),
        uncited,
    ]
    payload = _payload(bullets=bullets, sources=[_in_window_source("src-1"), _in_window_source("src-2")])
    result = validate_recap(payload, period_start=PERIOD_START, period_end=PERIOD_END, ticker="AAPL")
    assert result.ok is False
    assert REASON_BULLET_MISSING_CITATION in result.failures


def test_citation_referencing_unknown_source_fails():
    from services.ticker_recap.validator import REASON_CITATION_UNKNOWN_SOURCE, validate_recap

    # TickerRecapPayload's model_validator rejects unknown source_ids at construction;
    # model_construct bypasses it to prove the validator also guards before persist.
    payload = TickerRecapPayload.model_construct(
        ticker="AAPL",
        cadence="daily",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        summary="AAPL rose on strong demand.",
        bullets=[
            Bullet(text="b1", citations=[Citation(source_id="src-1")]),
            Bullet(text="b2", citations=[Citation(source_id="src-2")]),
            Bullet(text="b3", citations=[Citation(source_id="ghost")]),
        ],
        sources=[_in_window_source("src-1"), _in_window_source("src-2")],
    )
    result = validate_recap(payload, period_start=PERIOD_START, period_end=PERIOD_END, ticker="AAPL")
    assert result.ok is False
    assert REASON_CITATION_UNKNOWN_SOURCE in result.failures


def test_out_of_window_cited_source_fails():
    from services.ticker_recap.validator import REASON_OUT_OF_WINDOW, validate_recap

    stale = _source(source_id="src-3", published_at=datetime(2026, 6, 10, 10, 0, tzinfo=UTC))
    sources = [_in_window_source("src-1"), _in_window_source("src-2"), stale]
    bullets = [Bullet(text=f"b{i}", citations=[Citation(source_id=f"src-{i}")]) for i in range(1, 4)]
    payload = _payload(bullets=bullets, sources=sources)
    result = validate_recap(payload, period_start=PERIOD_START, period_end=PERIOD_END, ticker="AAPL")
    assert result.ok is False
    assert REASON_OUT_OF_WINDOW in result.failures


def test_grace_day_within_one_day_passes():
    from services.ticker_recap.validator import REASON_OUT_OF_WINDOW, validate_recap

    plus_one = _source(source_id="src-3", published_at=datetime(2026, 6, 27, 10, 0, tzinfo=UTC))
    sources = [_in_window_source("src-1"), _in_window_source("src-2"), plus_one]
    bullets = [Bullet(text=f"b{i}", citations=[Citation(source_id=f"src-{i}")]) for i in range(1, 4)]
    payload = _payload(bullets=bullets, sources=sources)
    result = validate_recap(payload, period_start=PERIOD_START, period_end=PERIOD_END, ticker="AAPL")
    assert REASON_OUT_OF_WINDOW not in result.failures


def test_too_many_bullets_fails():
    from services.ticker_recap.validator import REASON_BULLET_COUNT, validate_recap

    payload = _payload(
        bullets=[Bullet(text=f"b{i}", citations=[Citation(source_id="src-1")]) for i in range(1, 8)],
        sources=[_in_window_source("src-1")],
    )
    result = validate_recap(payload, period_start=PERIOD_START, period_end=PERIOD_END, ticker="AAPL")
    assert result.ok is False
    assert REASON_BULLET_COUNT in result.failures
