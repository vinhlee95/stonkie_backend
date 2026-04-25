import json
from datetime import date, datetime
from pathlib import Path

import httpx

from services.market_recap.tavily_client import TavilyClient


def test_tavily_client_normalizes_candidates_from_http_response():
    fixture_path = Path(__file__).parent / "fixtures" / "tavily" / "search_response.json"
    payload = json.loads(fixture_path.read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://api.tavily.com/search")
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content.decode("utf-8"))
        assert body["search_depth"] == "basic"
        assert body["topic"] == "news"
        assert body["max_results"] == 5
        assert body["start_date"] == "2026-04-20"
        assert body["end_date"] == "2026-04-24"
        return httpx.Response(status_code=200, json=payload)

    client = TavilyClient(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    candidates = client.search(
        query="US market recap",
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
    )

    assert len(candidates) == 2

    first = candidates[0]
    assert first.title == "S&P 500 wraps week higher"
    assert first.provider == "tavily"
    assert first.published_date == datetime.fromisoformat("2026-04-24T20:30:00+00:00")
    assert first.raw_content == "Long-form Reuters content"
    assert first.snippet == "US equities ended the week higher on Friday."
    assert first.score == 0.91

    second = candidates[1]
    assert second.raw_content == ""
    assert second.published_date == datetime.fromisoformat("2026-04-24T21:00:00+00:00")


def test_tavily_client_accepts_rfc2822_published_date():
    payload = {
        "results": [
            {
                "title": "RFC2822 date item",
                "url": "https://www.reuters.com/markets/us/rfc-date",
                "content": "Snippet",
                "published_date": "Mon, 20 Apr 2026 01:29:33 GMT",
                "raw_content": "Body",
                "score": 0.55,
            }
        ]
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, json=payload)

    client = TavilyClient(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    candidates = client.search(
        query="US market recap",
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
    )

    assert len(candidates) == 1
    assert candidates[0].published_date == datetime.fromisoformat("2026-04-20T01:29:33+00:00")
