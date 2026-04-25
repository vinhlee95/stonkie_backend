from datetime import UTC, date, datetime

from services.market_recap.schemas import Bullet, Citation, RecapPayload, Source


def _source(
    *,
    source_id: str,
    url: str,
    published_at: datetime,
    title: str | None = None,
) -> Source:
    return Source(
        id=source_id,
        url=url,
        title=title or source_id,
        publisher="publisher",
        published_at=published_at,
        fetched_at=published_at,
    )


def _payload(*, bullets: list[Bullet], sources: list[Source]) -> RecapPayload:
    return RecapPayload(
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        summary="summary",
        bullets=bullets,
        sources=sources,
    )


def test_well_formed_payload_passes():
    from services.market_recap.validator import validate_recap

    s1 = _source(
        source_id="src-1",
        url="https://www.reuters.com/world/one",
        published_at=datetime(2026, 4, 23, 10, 0, tzinfo=UTC),
    )
    s2 = _source(
        source_id="src-2",
        url="https://www.reuters.com/world/two",
        published_at=datetime(2026, 4, 24, 9, 0, tzinfo=UTC),
    )
    payload = _payload(
        bullets=[
            Bullet(text="b1", citations=[Citation(source_id="src-1")]),
            Bullet(text="b2", citations=[Citation(source_id="src-2")]),
        ],
        sources=[s1, s2],
    )
    result = validate_recap(payload, period_start=date(2026, 4, 20), period_end=date(2026, 4, 24))
    assert result.ok is True
    assert result.failures == []


def test_out_of_window_cited_source_fails():
    from services.market_recap.validator import REASON_OUT_OF_WINDOW, validate_recap

    s1 = _source(
        source_id="src-1",
        url="https://www.reuters.com/world/one",
        published_at=datetime(2026, 4, 14, 10, 0, tzinfo=UTC),
    )
    payload = _payload(
        bullets=[Bullet(text="b1", citations=[Citation(source_id="src-1")])],
        sources=[s1],
    )
    result = validate_recap(payload, period_start=date(2026, 4, 20), period_end=date(2026, 4, 24))
    assert REASON_OUT_OF_WINDOW in result.failures


def test_grace_day_within_one_day_passes():
    from services.market_recap.validator import REASON_OUT_OF_WINDOW, validate_recap

    plus_one = _source(
        source_id="src-1",
        url="https://www.reuters.com/world/one",
        published_at=datetime(2026, 4, 25, 10, 0, tzinfo=UTC),
    )
    payload_ok = _payload(
        bullets=[Bullet(text="b1", citations=[Citation(source_id="src-1")])],
        sources=[plus_one],
    )
    result_ok = validate_recap(payload_ok, period_start=date(2026, 4, 20), period_end=date(2026, 4, 24))
    assert REASON_OUT_OF_WINDOW not in result_ok.failures

    plus_two = _source(
        source_id="src-2",
        url="https://www.reuters.com/world/two",
        published_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
    )
    payload_fail = _payload(
        bullets=[Bullet(text="b1", citations=[Citation(source_id="src-2")])],
        sources=[plus_two],
    )
    result_fail = validate_recap(payload_fail, period_start=date(2026, 4, 20), period_end=date(2026, 4, 24))
    assert REASON_OUT_OF_WINDOW in result_fail.failures


def test_bullet_with_no_allowlisted_source_fails():
    from services.market_recap.validator import REASON_BULLET_MISSING_ALLOWLISTED, validate_recap

    s1 = _source(
        source_id="src-1",
        url="https://random-blog.example/post",
        published_at=datetime(2026, 4, 24, 10, 0, tzinfo=UTC),
    )
    payload = _payload(
        bullets=[Bullet(text="b1", citations=[Citation(source_id="src-1")])],
        sources=[s1],
    )
    result = validate_recap(payload, period_start=date(2026, 4, 20), period_end=date(2026, 4, 24))
    assert REASON_BULLET_MISSING_ALLOWLISTED in result.failures


def test_bullet_with_mixed_sources_passes_when_at_least_one_allowlisted():
    from services.market_recap.validator import REASON_BULLET_MISSING_ALLOWLISTED, validate_recap

    s1 = _source(
        source_id="src-1",
        url="https://www.reuters.com/world/one",
        published_at=datetime(2026, 4, 24, 10, 0, tzinfo=UTC),
    )
    s2 = _source(
        source_id="src-2",
        url="https://random-blog.example/post",
        published_at=datetime(2026, 4, 24, 10, 0, tzinfo=UTC),
    )
    payload = _payload(
        bullets=[Bullet(text="b1", citations=[Citation(source_id="src-1"), Citation(source_id="src-2")])],
        sources=[s1, s2],
    )
    result = validate_recap(payload, period_start=date(2026, 4, 20), period_end=date(2026, 4, 24))
    assert REASON_BULLET_MISSING_ALLOWLISTED not in result.failures


def test_unique_source_floor_warning():
    from services.market_recap.validator import validate_recap

    s1 = _source(
        source_id="src-1",
        url="https://www.reuters.com/world/one",
        published_at=datetime(2026, 4, 24, 10, 0, tzinfo=UTC),
    )
    payload_warn = _payload(
        bullets=[
            Bullet(text="b1", citations=[Citation(source_id="src-1")]),
            Bullet(text="b2", citations=[Citation(source_id="src-1")]),
        ],
        sources=[s1],
    )
    result_warn = validate_recap(payload_warn, period_start=date(2026, 4, 20), period_end=date(2026, 4, 24))
    assert result_warn.ok is True
    assert result_warn.warnings

    s2 = _source(
        source_id="src-2",
        url="https://www.reuters.com/world/two",
        published_at=datetime(2026, 4, 24, 10, 0, tzinfo=UTC),
    )
    s3 = _source(
        source_id="src-3",
        url="https://www.apnews.com/article/three",
        published_at=datetime(2026, 4, 24, 10, 0, tzinfo=UTC),
    )
    payload_ok = _payload(
        bullets=[
            Bullet(text="b1", citations=[Citation(source_id="src-1")]),
            Bullet(text="b2", citations=[Citation(source_id="src-2")]),
            Bullet(text="b3", citations=[Citation(source_id="src-3")]),
        ],
        sources=[s1, s2, s3],
    )
    result_ok = validate_recap(payload_ok, period_start=date(2026, 4, 20), period_end=date(2026, 4, 24))
    assert result_ok.warnings == []


def test_failures_accumulate_not_short_circuit():
    from services.market_recap.validator import (
        REASON_BULLET_MISSING_ALLOWLISTED,
        REASON_OUT_OF_WINDOW,
        validate_recap,
    )

    s1 = _source(
        source_id="src-1",
        url="https://random-blog.example/post",
        published_at=datetime(2026, 4, 10, 10, 0, tzinfo=UTC),
    )
    payload = _payload(
        bullets=[Bullet(text="b1", citations=[Citation(source_id="src-1")])],
        sources=[s1],
    )
    result = validate_recap(payload, period_start=date(2026, 4, 20), period_end=date(2026, 4, 24))
    assert REASON_OUT_OF_WINDOW in result.failures
    assert REASON_BULLET_MISSING_ALLOWLISTED in result.failures


def test_empty_summary_bullets_sources_hard_fail():
    from services.market_recap.validator import (
        REASON_EMPTY_BULLETS,
        REASON_EMPTY_SOURCES,
        REASON_EMPTY_SUMMARY,
        validate_recap,
    )

    payload = RecapPayload(
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        summary="",
        bullets=[],
        sources=[],
    )

    result = validate_recap(payload, period_start=date(2026, 4, 20), period_end=date(2026, 4, 24))
    assert result.ok is False
    assert REASON_EMPTY_SUMMARY in result.failures
    assert REASON_EMPTY_BULLETS in result.failures
    assert REASON_EMPTY_SOURCES in result.failures


def test_vn_allowlisted_source_passes_when_market_is_vn():
    from services.market_recap.validator import REASON_BULLET_MISSING_ALLOWLISTED, validate_recap

    s1 = _source(
        source_id="src-1",
        url="https://cafef.vn/thi-truong-chung-khoan.chn",
        published_at=datetime(2026, 4, 24, 10, 0, tzinfo=UTC),
    )
    payload = _payload(
        bullets=[Bullet(text="b1", citations=[Citation(source_id="src-1")])],
        sources=[s1],
    )
    result = validate_recap(payload, period_start=date(2026, 4, 20), period_end=date(2026, 4, 24), market="VN")
    assert REASON_BULLET_MISSING_ALLOWLISTED not in result.failures


def test_vn_summary_requires_index_macro_and_money_flow_markers():
    from services.market_recap.validator import (
        REASON_VN_INDEX_MISSING,
        REASON_VN_MACRO_CONTEXT_MISSING,
        REASON_VN_MONEY_FLOW_MISSING,
        validate_recap,
    )

    s1 = _source(
        source_id="src-1",
        url="https://vietstock.vn/chung-khoan.htm",
        published_at=datetime(2026, 4, 24, 10, 0, tzinfo=UTC),
    )
    bad_payload = RecapPayload(
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        summary="Thi truong bien dong, tam ly than trong.",
        bullets=[Bullet(text="b1", citations=[Citation(source_id="src-1")])],
        sources=[s1],
    )
    bad = validate_recap(bad_payload, period_start=date(2026, 4, 20), period_end=date(2026, 4, 24), market="VN")
    assert REASON_VN_INDEX_MISSING in bad.failures
    assert REASON_VN_MACRO_CONTEXT_MISSING in bad.failures
    assert REASON_VN_MONEY_FLOW_MISSING in bad.failures

    good_payload = RecapPayload(
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        summary=(
            "VN-Index declined as macroeconomic concerns on inflation and exchange rate persisted. "
            "Money flow and liquidity weakened with lower turnover and selective sector rotation."
        ),
        bullets=[Bullet(text="b1", citations=[Citation(source_id="src-1")])],
        sources=[s1],
    )
    good = validate_recap(good_payload, period_start=date(2026, 4, 20), period_end=date(2026, 4, 24), market="VN")
    assert REASON_VN_INDEX_MISSING not in good.failures
    assert REASON_VN_MACRO_CONTEXT_MISSING not in good.failures
    assert REASON_VN_MONEY_FLOW_MISSING not in good.failures
