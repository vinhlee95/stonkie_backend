from contextlib import contextmanager
from datetime import UTC, date, datetime

from models.market_recap import MarketRecap
from services.market_recap.orchestrator import run_market_recap
from services.market_recap.recap_generator import GeneratorError, GeneratorResult
from services.market_recap.schemas import (
    Bullet,
    Candidate,
    Citation,
    RecapPayload,
    RetrievalResult,
    RetrievalStats,
    Source,
)
from services.market_recap.validator import REASON_OUT_OF_WINDOW, ValidationResult


def _retrieval_result() -> RetrievalResult:
    return RetrievalResult(
        candidates=[
            Candidate(
                title="Reuters A",
                url="https://www.reuters.com/markets/a",
                snippet="s",
                published_date=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
                raw_content="body",
                score=0.9,
                provider="tavily",
            )
        ],
        stats=RetrievalStats(
            queries_total=1,
            results_total=1,
            deduped=1,
            with_raw_content=1,
            allowlisted=1,
            ranked_top_k=1,
        ),
    )


def _payload(summary: str = "weekly summary") -> RecapPayload:
    source = Source(
        id="src-1",
        url="https://www.reuters.com/markets/a",
        title="Reuters A",
        publisher="reuters.com",
        published_at=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
        fetched_at=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
    )
    return RecapPayload(
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        summary=summary,
        bullets=[Bullet(text="b1", citations=[Citation(source_id="src-1")])],
        sources=[source],
    )


def _session_factory(db_session):
    @contextmanager
    def _factory():
        yield db_session

    return _factory


def test_run_market_recap_success_persists_row(db_session):
    result = run_market_recap(
        market="US",
        cadence="weekly",
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        session_factory=_session_factory(db_session),
        retrieve_fn=lambda **_: _retrieval_result(),
        generate_fn=lambda **kwargs: GeneratorResult(payload=_payload(), model="test-model", raw_model_output="raw"),
        validate_fn=lambda **_: ValidationResult(ok=True, failures=[], warnings=[]),
    )

    assert result.status == "inserted"
    assert result.inserted is True
    assert result.attempts == 1
    assert result.recap_id is not None
    assert db_session.query(MarketRecap).count() == 1


def test_run_market_recap_forwards_cadence_to_generate_fn(db_session):
    captured: dict = {}

    def _generate(**kwargs):
        captured.update(kwargs)
        return GeneratorResult(payload=_payload(), model="test-model", raw_model_output="raw")

    run_market_recap(
        market="US",
        cadence="daily",
        period_start=date(2026, 4, 23),
        period_end=date(2026, 4, 23),
        session_factory=_session_factory(db_session),
        retrieve_fn=lambda **_: _retrieval_result(),
        generate_fn=_generate,
        validate_fn=lambda **_: ValidationResult(ok=True, failures=[], warnings=[]),
    )

    assert captured.get("cadence") == "daily"


def test_run_market_recap_validation_failure_retries_and_skips_insert(db_session):
    result = run_market_recap(
        market="US",
        cadence="weekly",
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        max_attempts=3,
        session_factory=_session_factory(db_session),
        retrieve_fn=lambda **_: _retrieval_result(),
        generate_fn=lambda **kwargs: GeneratorResult(payload=_payload(), model="test-model", raw_model_output="raw"),
        validate_fn=lambda **_: ValidationResult(
            ok=False,
            failures=[REASON_OUT_OF_WINDOW],
            warnings=[],
        ),
    )

    assert result.status == "validation_failed"
    assert result.inserted is False
    assert result.attempts == 3
    assert result.validation_failures == [REASON_OUT_OF_WINDOW]
    assert db_session.query(MarketRecap).count() == 0


def test_run_market_recap_generation_failure_retries_and_skips_insert(db_session):
    result = run_market_recap(
        market="US",
        cadence="weekly",
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        max_attempts=2,
        session_factory=_session_factory(db_session),
        retrieve_fn=lambda **_: _retrieval_result(),
        generate_fn=lambda **kwargs: (_ for _ in ()).throw(GeneratorError("bad generation")),
        validate_fn=lambda **_: ValidationResult(ok=True, failures=[], warnings=[]),
    )

    assert result.status == "generation_failed"
    assert result.inserted is False
    assert result.attempts == 2
    assert result.validation_failures == []
    assert db_session.query(MarketRecap).count() == 0


def test_run_market_recap_vn_daily_path_succeeds_with_mocked_brave(db_session):
    retrieval = RetrievalResult(
        candidates=[
            Candidate(
                title="VN source",
                url="https://cafef.vn/thi-truong/a",
                snippet="s",
                published_date=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
                raw_content="body",
                score=0.0,
                provider="brave",
            )
        ],
        stats=RetrievalStats(
            queries_total=1,
            results_total=1,
            deduped=1,
            with_raw_content=1,
            allowlisted=1,
            ranked_top_k=1,
        ),
    )

    result = run_market_recap(
        market="VN",
        cadence="daily",
        period_start=date(2026, 4, 24),
        period_end=date(2026, 4, 24),
        session_factory=_session_factory(db_session),
        retrieve_fn=lambda **_: retrieval,
        generate_fn=lambda **kwargs: GeneratorResult(
            payload=_payload(summary="VN-Index macro money flow"), model="test-model", raw_model_output="raw"
        ),
        validate_fn=lambda **_: ValidationResult(ok=True, failures=[], warnings=[]),
    )
    assert result.status == "inserted"
    assert result.inserted is True
