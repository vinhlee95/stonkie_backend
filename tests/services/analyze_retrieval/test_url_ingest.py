from __future__ import annotations

import json
import logging

import pytest


class _StubTavilyExtractClient:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[dict] = []

    def extract(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_ingest_url_uses_query_focused_tavily_extract_and_logs_metadata(caplog) -> None:
    from services.analyze_retrieval.url_ingest import ingest_url

    client = _StubTavilyExtractClient(
        {
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
        }
    )

    with caplog.at_level(logging.INFO, logger="services.analyze_retrieval.url_ingest"):
        result = ingest_url(
            url="https://sec.gov/aapl-q3.pdf",
            question="What drove margin?",
            request_id="req-1",
            source_kind="filing",
            client=client,
        )

    assert client.calls == [
        {
            "urls": ["https://sec.gov/aapl-q3.pdf"],
            "query": "What drove margin?",
            "extract_depth": "advanced",
            "chunks_per_source": 5,
            "format": "markdown",
            "timeout": 30,
        }
    ]
    assert [source.url for source in result.sources] == ["https://sec.gov/aapl-q3.pdf"]
    assert result.selected_passages[0].content == "Revenue grew 6%."
    assert result.selected_passages[1].content == "Margin expanded on services mix."

    log_record = next(record for record in caplog.records if "analyze_v2_url_ingest" in record.message)
    log_payload = json.loads(log_record.message)
    assert log_payload["event"] == "analyze_v2_url_ingest"
    assert log_payload["successful_urls"] == ["https://sec.gov/aapl-q3.pdf"]
    assert log_payload["chunk_count"] == 2
    assert log_record.provider == "tavily"
    assert log_record.request_id == "req-1"
    assert log_record.requested_urls == ["https://sec.gov/aapl-q3.pdf"]
    assert log_record.successful_urls == ["https://sec.gov/aapl-q3.pdf"]
    assert log_record.failed_urls == []
    assert log_record.chunk_count == 2
    assert log_record.tavily_request_id == "tvly-1"
    assert not hasattr(log_record, "raw_content")
    assert "Revenue grew" not in str(log_record.__dict__)


def test_ingest_url_raises_when_tavily_returns_no_usable_chunks() -> None:
    from services.analyze_retrieval.url_ingest import UrlIngestError, ingest_url

    client = _StubTavilyExtractClient(
        {
            "results": [{"url": "https://example.com/report.pdf", "raw_content": "   "}],
            "failed_results": [],
        }
    )

    with pytest.raises(UrlIngestError):
        ingest_url(
            url="https://example.com/report.pdf",
            question="Summarize it",
            request_id="req-empty",
            source_kind="user_url",
            client=client,
        )
