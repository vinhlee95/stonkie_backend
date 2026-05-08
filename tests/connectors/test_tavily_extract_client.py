from __future__ import annotations

from connectors.tavily_extract_client import TavilyExtractClient


class _Response:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {
            "results": [
                {
                    "url": "https://sec.gov/report.htm",
                    "title": "AAPL 10-Q",
                    "raw_content": "First relevant paragraph. [...] Second relevant paragraph.",
                }
            ],
            "response_time": 0.42,
            "request_id": "tvly-request",
            "usage": {"credits": 1},
        }


def test_extract_url_posts_single_url_with_api_timeout_buffer_and_returns_parsed_result(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _Response()

    monkeypatch.setattr("connectors.tavily_extract_client.requests.post", fake_post)

    client = TavilyExtractClient(api_key="test-key", timeout_buffer=5.0)
    result = client.extract_url(
        url="https://sec.gov/report.htm",
        query="What drove margin?",
        extract_depth="advanced",
        chunks_per_source=5,
        format="markdown",
        timeout=30,
    )

    assert calls == [
        {
            "url": "https://api.tavily.com/extract",
            "headers": {"Authorization": "Bearer test-key"},
            "json": {
                "urls": ["https://sec.gov/report.htm"],
                "query": "What drove margin?",
                "extract_depth": "advanced",
                "chunks_per_source": 5,
                "format": "markdown",
                "timeout": 30,
                "include_images": False,
                "include_favicon": True,
                "include_usage": True,
            },
            "timeout": 35.0,
        }
    ]
    assert result.source.url == "https://sec.gov/report.htm"
    assert [passage.content for passage in result.selected_passages] == [
        "First relevant paragraph.",
        "Second relevant paragraph.",
    ]
    assert result.metadata["tavily_request_id"] == "tvly-request"
