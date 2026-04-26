import json
import logging
from contextlib import contextmanager
from datetime import UTC, date, datetime

import pytest

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

REQUIRED_FIELDS = {
    "run_id",
    "market",
    "cadence",
    "period_start",
    "period_end",
    "queries_total",
    "results_total",
    "fetched_ok",
    "date_in_window_count",
    "allowlisted_count",
    "cited_count",
    "validation_fail_reason",
    "inserted",
}


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
            queries_total=2,
            results_total=5,
            deduped=4,
            with_raw_content=3,
            allowlisted=2,
            ranked_top_k=1,
        ),
    )


def _payload() -> RecapPayload:
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
        summary="weekly summary",
        bullets=[Bullet(text="b1", citations=[Citation(source_id="src-1")])],
        sources=[source],
    )


def _session_factory(db_session):
    @contextmanager
    def _factory():
        yield db_session

    return _factory


def _events(caplog, name):
    return [record for record in caplog.records if getattr(record, "event", None) == name]


def _fields(record):
    return getattr(record, "fields")


@pytest.fixture(autouse=True)
def _capture(caplog):
    caplog.set_level(logging.INFO)


def test_success_emits_start_and_outcome_with_required_fields(db_session, caplog):
    run_market_recap(
        market="US",
        cadence="weekly",
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        session_factory=_session_factory(db_session),
        retrieve_fn=lambda **_: _retrieval_result(),
        generate_fn=lambda **_: GeneratorResult(payload=_payload(), model="test-model", raw_model_output="raw"),
        validate_fn=lambda **_: ValidationResult(ok=True, failures=[], warnings=[]),
    )

    starts = _events(caplog, "recap.run.start")
    outcomes = _events(caplog, "recap.run.outcome")
    assert len(starts) == 1
    assert len(outcomes) == 1

    start_fields = _fields(starts[0])
    outcome_fields = _fields(outcomes[0])

    assert start_fields["run_id"] == outcome_fields["run_id"]
    assert start_fields["market"] == "US"
    assert start_fields["cadence"] == "weekly"
    assert start_fields["provider"] == "brave"
    assert start_fields["period_start"] == "2026-04-20"
    assert start_fields["period_end"] == "2026-04-24"

    assert REQUIRED_FIELDS.issubset(outcome_fields.keys())
    assert outcome_fields["queries_total"] == 2
    assert outcome_fields["results_total"] == 5
    assert outcome_fields["fetched_ok"] == 3
    assert outcome_fields["date_in_window_count"] == 5
    assert outcome_fields["allowlisted_count"] == 2
    assert outcome_fields["cited_count"] == 1
    assert outcome_fields["validation_fail_reason"] is None
    assert outcome_fields["inserted"] is True
    assert outcome_fields["status"] == "inserted"
    assert outcome_fields["provider"] == "brave"

    json.dumps(outcome_fields)


def test_validation_failure_logs_reason_and_skip(db_session, caplog):
    run_market_recap(
        market="US",
        cadence="weekly",
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        max_attempts=2,
        session_factory=_session_factory(db_session),
        retrieve_fn=lambda **_: _retrieval_result(),
        generate_fn=lambda **_: GeneratorResult(payload=_payload(), model="test-model", raw_model_output="raw"),
        validate_fn=lambda **_: ValidationResult(ok=False, failures=[REASON_OUT_OF_WINDOW], warnings=[]),
    )

    outcome = _events(caplog, "recap.run.outcome")
    assert len(outcome) == 1
    fields = _fields(outcome[0])
    assert fields["status"] == "validation_failed"
    assert fields["inserted"] is False
    assert fields["validation_fail_reason"] == REASON_OUT_OF_WINDOW
    assert fields["cited_count"] == 1


def test_generation_failure_logs_zero_cited_and_skip(db_session, caplog):
    run_market_recap(
        market="US",
        cadence="weekly",
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        max_attempts=2,
        session_factory=_session_factory(db_session),
        retrieve_fn=lambda **_: _retrieval_result(),
        generate_fn=lambda **_: (_ for _ in ()).throw(GeneratorError("boom")),
        validate_fn=lambda **_: ValidationResult(ok=True, failures=[], warnings=[]),
    )

    outcome = _events(caplog, "recap.run.outcome")
    assert len(outcome) == 1
    fields = _fields(outcome[0])
    assert fields["status"] == "generation_failed"
    assert fields["inserted"] is False
    assert fields["cited_count"] == 0
    assert fields["validation_fail_reason"] is None


def test_run_id_is_stable_hex_across_events(db_session, caplog):
    run_market_recap(
        market="US",
        cadence="weekly",
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        session_factory=_session_factory(db_session),
        retrieve_fn=lambda **_: _retrieval_result(),
        generate_fn=lambda **_: GeneratorResult(payload=_payload(), model="test-model", raw_model_output="raw"),
        validate_fn=lambda **_: ValidationResult(ok=True, failures=[], warnings=[]),
    )

    start = _fields(_events(caplog, "recap.run.start")[0])
    outcome = _fields(_events(caplog, "recap.run.outcome")[0])
    assert start["run_id"] == outcome["run_id"]
    assert isinstance(start["run_id"], str)
    int(start["run_id"], 16)
    assert len(start["run_id"]) == 32


def test_vn_daily_logs_provider_brave_and_daily_cadence(db_session, caplog):
    run_market_recap(
        market="VN",
        cadence="daily",
        period_start=date(2026, 4, 24),
        period_end=date(2026, 4, 24),
        session_factory=_session_factory(db_session),
        retrieve_fn=lambda **_: _retrieval_result(),
        generate_fn=lambda **_: GeneratorResult(payload=_payload(), model="test-model", raw_model_output="raw"),
        validate_fn=lambda **_: ValidationResult(ok=True, failures=[], warnings=[]),
    )
    start = _fields(_events(caplog, "recap.run.start")[0])
    outcome = _fields(_events(caplog, "recap.run.outcome")[0])
    assert start["provider"] == "brave"
    assert outcome["provider"] == "brave"
    assert outcome["cadence"] == "daily"
