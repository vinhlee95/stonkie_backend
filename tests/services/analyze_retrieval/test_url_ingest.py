from __future__ import annotations

import json
import logging

import pytest


class _StubTavilyExtractClient:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    def extract_url(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


def test_ingest_url_uses_query_focused_tavily_extract_and_logs_metadata(caplog) -> None:
    from connectors.tavily_extract_client import parse_tavily_extract_response
    from services.analyze_retrieval.url_ingest import ingest_url

    result = parse_tavily_extract_response(
        url="https://sec.gov/aapl-q3.pdf",
        response={
            "results": [
                {
                    "url": "https://sec.gov/aapl-q3.pdf",
                    "title": "AAPL Q3 10-Q",
                    "raw_content": "Revenue grew 6%. [...] Margin expanded on services mix.",
                    "favicon": "https://sec.gov/favicon.ico",
                }
            ],
            "failed_results": [],
            "response_time": 1.23,
            "request_id": "tvly-1",
            "usage": {"credits": 1},
        },
    )
    client = _StubTavilyExtractClient(result=result)

    with caplog.at_level(logging.INFO, logger="services.analyze_retrieval.url_ingest"):
        ingest_result = ingest_url(
            url="https://sec.gov/aapl-q3.pdf",
            question="What drove margin?",
            request_id="req-1",
            source_kind="filing",
            client=client,
        )

    assert client.calls == [
        {
            "url": "https://sec.gov/aapl-q3.pdf",
            "query": "What drove margin?",
            "extract_depth": "advanced",
            "chunks_per_source": 5,
            "format": "markdown",
            "timeout": 30,
        }
    ]
    assert ingest_result.source.url == "https://sec.gov/aapl-q3.pdf"
    assert ingest_result.selected_passages[0].content == "Revenue grew 6%."
    assert ingest_result.selected_passages[1].content == "Margin expanded on services mix."

    log_record = next(record for record in caplog.records if "analyze_v2_url_ingest" in record.message)
    log_payload = json.loads(log_record.message)
    assert log_payload["event"] == "analyze_v2_url_ingest"
    assert log_payload["requested_url"] == "https://sec.gov/aapl-q3.pdf"
    assert log_payload["successful_url"] == "https://sec.gov/aapl-q3.pdf"
    assert log_payload["failed_url"] is None
    assert log_payload["source_id"] == ingest_result.source.id
    assert log_payload["chunk_count"] == 2
    assert log_record.provider == "tavily"
    assert log_record.request_id == "req-1"
    assert log_record.requested_url == "https://sec.gov/aapl-q3.pdf"
    assert log_record.successful_url == "https://sec.gov/aapl-q3.pdf"
    assert log_record.failed_url is None
    assert log_record.chunk_count == 2
    assert log_record.tavily_request_id == "tvly-1"
    assert not hasattr(log_record, "raw_content")
    assert "Revenue grew" not in str(log_record.__dict__)


def test_ingest_url_raises_when_tavily_returns_no_usable_chunks() -> None:
    from connectors.tavily_extract_client import TavilyExtractError
    from services.analyze_retrieval.url_ingest import UrlIngestError, ingest_url

    client = _StubTavilyExtractClient(
        error=TavilyExtractError(
            "Could not read the document URL.", metadata={"failed_url": "https://example.com/report.pdf"}
        )
    )

    with pytest.raises(UrlIngestError):
        ingest_url(
            url="https://example.com/report.pdf",
            question="Summarize it",
            request_id="req-empty",
            source_kind="user_url",
            client=client,
        )
