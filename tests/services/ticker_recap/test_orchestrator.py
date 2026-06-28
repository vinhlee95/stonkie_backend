from datetime import UTC, date, datetime

from connectors.ticker_recap import UpsertResult
from services.market_recap.schemas import Candidate, RetrievalResult, RetrievalStats
from services.ticker_recap.recap_generator import GeneratorResult
from services.ticker_recap.schemas import Bullet, Citation, Source, TickerRecapPayload
from services.ticker_recap.validator import ValidationResult

PERIOD_START = date(2026, 6, 22)
PERIOD_END = date(2026, 6, 26)


def _payload(summary: str = "AAPL rose.") -> TickerRecapPayload:
    source = Source(
        id="src-1",
        url="https://www.reuters.com/markets/aapl",
        title="Apple rallies",
        publisher="Reuters",
        published_at=datetime(2026, 6, 25, 16, 30, tzinfo=UTC),
        fetched_at=datetime(2026, 6, 26, 8, 0, tzinfo=UTC),
    )
    return TickerRecapPayload(
        ticker="AAPL",
        cadence="daily",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        summary=summary,
        bullets=[Bullet(text="Apple gained.", citations=[Citation(source_id="src-1")])],
        sources=[source],
    )


def _retrieval(*, candidates: list[Candidate] | None = None) -> RetrievalResult:
    cands = (
        candidates
        if candidates is not None
        else [Candidate(title="Apple rallies", url="https://www.reuters.com/markets/aapl", provider="brave")]
    )
    stats = RetrievalStats(
        queries_total=1,
        results_total=len(cands),
        deduped=0,
        with_raw_content=len(cands),
        allowlisted=len(cands),
        ranked_top_k=len(cands),
    )
    return RetrievalResult(candidates=cands, stats=stats, query_snapshots=[])


def _generated() -> GeneratorResult:
    return GeneratorResult(payload=_payload(), model="fake-model", raw_model_output="[RECAP_JSON]{}[/RECAP_JSON]")


class FakeConnector:
    def __init__(self, result: UpsertResult | None = None):
        self.result = result or UpsertResult(inserted=True, replaced=False, recap_id=42)
        self.upsert_calls: list[dict] = []

    def upsert_recap(self, **kwargs):
        self.upsert_calls.append(kwargs)
        return self.result


def _run(**overrides):
    from services.ticker_recap.orchestrator import run_ticker_recap

    connector = overrides.pop("recap_connector", None) or FakeConnector()
    kwargs = dict(
        ticker="AAPL",
        company_name="Apple Inc.",
        cadence="daily",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        price_fn=lambda ticker: {"change_percent": 1.2, "trading_date": "2026-06-26"},
        query_fn=lambda **_: "why did AAPL rise",
        retrieve_fn=lambda **_: _retrieval(),
        generate_fn=lambda **_: _generated(),
        validate_fn=lambda **_: ValidationResult(ok=True, failures=[], warnings=[]),
        recap_connector=connector,
    )
    kwargs.update(overrides)
    return run_ticker_recap(**kwargs), connector


def test_happy_path_inserts():
    result, connector = _run()

    assert result.status == "inserted"
    assert result.inserted is True
    assert result.recap_id == 42
    assert len(connector.upsert_calls) == 1


def test_missing_price_skips_persist(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        result, connector = _run(price_fn=lambda ticker: None)

    assert result.status == "skipped_no_price"
    assert result.inserted is False
    assert result.recap_id is None
    assert connector.upsert_calls == []
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_zero_candidates_skips_persist(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        result, connector = _run(retrieve_fn=lambda **_: _retrieval(candidates=[]))

    assert result.status == "skipped_no_results"
    assert result.inserted is False
    assert connector.upsert_calls == []
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_generation_fails_every_attempt():
    from services.ticker_recap.recap_generator import GeneratorError

    def _boom(**_):
        raise GeneratorError("bad output")

    result, connector = _run(generate_fn=_boom)

    assert result.status == "generation_failed"
    assert result.inserted is False
    assert result.attempts == 3
    assert connector.upsert_calls == []


def test_validation_fails_every_attempt():
    bad = ValidationResult(ok=False, failures=["empty_summary"], warnings=[])
    result, connector = _run(validate_fn=lambda **_: bad)

    assert result.status == "validation_failed"
    assert result.inserted is False
    assert result.attempts == 3
    assert result.validation_failures == ["empty_summary"]
    assert connector.upsert_calls == []


def test_existing_recap_is_skipped_not_duplicated():
    connector = FakeConnector(UpsertResult(inserted=False, replaced=False, recap_id=7))
    result, connector = _run(recap_connector=connector)

    assert result.status == "skipped_existing"
    assert result.inserted is False
    assert result.recap_id == 7
    assert len(connector.upsert_calls) == 1


def test_price_change_and_query_passed_to_connector():
    price = {"change_percent": -1.64, "trading_date": "2026-06-26"}
    result, connector = _run(
        price_fn=lambda ticker: price,
        query_fn=lambda **_: "why did AAPL fall",
    )

    assert result.status == "inserted"
    call = connector.upsert_calls[0]
    assert call["price_change"] == price
    assert call["search_query"] == "why did AAPL fall"


def test_replace_sets_replaced_status():
    connector = FakeConnector(UpsertResult(inserted=True, replaced=True, recap_id=5))
    result, connector = _run(recap_connector=connector, replace=True)

    assert result.status == "replaced"
    assert result.inserted is True
    assert result.recap_id == 5
    assert connector.upsert_calls[0]["replace"] is True
