import json
import logging

from services.analyze_retrieval.observability import log_retrieval


def test_log_retrieval_emits_single_json_line_with_required_fields(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="app.analyze_retrieval"):
        log_retrieval(
            request_id="req-1",
            ticker="AAPL",
            market="GLOBAL",
            ranked_urls=["https://example.com/a", "https://example.com/b"],
            selected_source_ids=["s1", "s2"],
            brave_latency_ms=123,
        )

    assert len(caplog.records) == 1
    payload = json.loads(caplog.records[0].message)
    assert payload == {
        "brave_latency_ms": 123,
        "market": "GLOBAL",
        "ranked_urls": ["https://example.com/a", "https://example.com/b"],
        "request_id": "req-1",
        "selected_source_ids": ["s1", "s2"],
        "ticker": "AAPL",
    }


def test_log_retrieval_does_not_log_raw_brave_payload(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="app.analyze_retrieval"):
        log_retrieval(
            request_id="req-2",
            ticker="MSFT",
            market="GLOBAL",
            ranked_urls=["https://example.com/a"],
            selected_source_ids=["s1"],
            brave_latency_ms=55,
            raw_brave_response={"huge": "payload"},
        )

    payload = json.loads(caplog.records[0].message)
    assert "raw_brave_response" not in payload
