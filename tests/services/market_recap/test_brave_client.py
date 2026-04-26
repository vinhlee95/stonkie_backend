import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from services.market_recap.brave_client import BraveClient, _build_vn_goggle
from services.market_recap.source_policy import ALLOWLIST_BY_MARKET


def test_brave_client_normalizes_candidates_from_http_response():
    fixture_path = Path(__file__).parent / "fixtures" / "brave" / "llm_context_response.json"
    payload = json.loads(fixture_path.read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://api.search.brave.com/res/v1/llm/context?")
        assert request.url.params["q"] == "thi truong"
        assert request.url.params["country"] == "ALL"
        assert request.url.params["search_lang"] == "vi"
        assert request.url.params["count"] == "30"
        assert request.url.params["freshness"] == "2026-04-20to2026-04-24"
        assert request.url.params["goggles"] == _build_vn_goggle()
        assert request.headers["X-Subscription-Token"] == "test-key"
        return httpx.Response(status_code=200, json=payload)

    client = BraveClient(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    candidates = client.search(
        query="thi truong",
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        include_domains=["ignored.com"],
    )

    assert len(candidates) == 2
    assert candidates[0].provider == "brave"
    assert candidates[0].raw_content == (
        "VN-Index fluctuated in the afternoon session.\n\n" "Liquidity improved versus the prior day."
    )
    assert candidates[0].published_date == datetime(2026, 4, 24, 12, 0, tzinfo=UTC)
    assert candidates[0].score == 0.0

    assert candidates[1].provider == "brave"
    assert candidates[1].raw_content == "Foreign investors were net buyers.\n\nFinancials outperformed."
    assert candidates[1].published_date == datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
    assert candidates[1].score == 0.0


def test_brave_client_returns_empty_list_for_empty_results():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, json={"results": [], "grounding": {"generic": []}, "sources": {}})

    client = BraveClient(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.search("q", date(2026, 4, 20), date(2026, 4, 24)) == []


def test_build_vn_goggle_is_deterministic_snapshot():
    expected = "\n".join(f"$boost=3,site={domain}" for domain in sorted(ALLOWLIST_BY_MARKET["VN"]))
    assert _build_vn_goggle() == expected
