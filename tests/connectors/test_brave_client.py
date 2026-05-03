import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from connectors.brave_client import BraveClient
from services.analyze_retrieval.schemas import BraveRetrievalError


def _fixture_payload() -> dict:
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "market_recap"
        / "fixtures"
        / "brave"
        / "llm_context_response.json"
    )
    return json.loads(fixture_path.read_text())


def test_search_builds_expected_request_and_parses_candidates() -> None:
    payload = _fixture_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://api.search.brave.com/res/v1/llm/context?")
        assert request.url.params["q"] == "thi truong"
        assert request.url.params["country"] == "ALL"
        assert request.url.params["search_lang"] == "vi"
        assert request.url.params["count"] == "20"
        assert request.url.params["context_threshold_mode"] == "strict"
        assert request.url.params["goggles"] == "$boost=4,site=cafef.vn"
        assert "freshness" not in request.url.params
        assert request.headers["X-Subscription-Token"] == "test-key"
        return httpx.Response(status_code=200, json=payload)

    client = BraveClient(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    candidates = client.search(
        query="thi truong",
        country="ALL",
        search_lang="vi",
        goggle="$boost=4,site=cafef.vn",
    )

    assert len(candidates) == 2
    assert candidates[0].provider == "brave"
    assert candidates[0].url == "https://www.cafef.vn/thi-truong/vn-index-1.chn"
    assert candidates[0].title == "VN-Index closes mixed"
    assert candidates[0].published_date == datetime(2026, 4, 24, 12, 0, tzinfo=UTC)
    assert candidates[0].raw_content == (
        "VN-Index fluctuated in the afternoon session.\n\nLiquidity improved versus the prior day."
    )


def test_search_falls_back_to_grounding_and_sources_titles_when_results_are_empty() -> None:
    payload = {
        "results": [],
        "grounding": {
            "generic": [
                {
                    "url": "https://example.com/from-grounding",
                    "title": "Grounding title",
                    "snippets": ["Grounded content."],
                },
                {
                    "url": "https://example.com/from-sources",
                    "snippets": ["Source-backed content."],
                },
            ]
        },
        "sources": {
            "https://example.com/from-grounding": {
                "title": "Sources title should not win",
                "age": ["2026-04-24"],
            },
            "https://example.com/from-sources": {
                "title": "Sources title wins",
                "age": ["2026-04-23"],
            },
        },
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, json=payload)

    client = BraveClient(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    candidates = client.search(
        query="latest market news",
        country="US",
        search_lang="en",
        goggle="",
    )

    assert [candidate.title for candidate in candidates] == [
        "Grounding title",
        "Sources title wins",
    ]


def test_search_raises_brave_retrieval_error_on_http_failure() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=503, json={"error": "unavailable"})

    client = BraveClient(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(BraveRetrievalError):
        client.search(query="q", country="US", search_lang="en", goggle="")


def test_search_raises_brave_retrieval_error_on_unparseable_payload() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, json={"grounding": "bad-shape"})

    client = BraveClient(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(BraveRetrievalError):
        client.search(query="q", country="US", search_lang="en", goggle="")
