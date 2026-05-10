import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from services.analyze_retrieval.schemas import BraveRetrievalError


def _parse_sse(raw: bytes) -> list[dict]:
    events: list[dict] = []
    for block in raw.decode("utf-8").strip().split("\n\n"):
        if block.strip():
            events.append(json.loads(block))
    return events


def _recap():
    return SimpleNamespace(
        id=91,
        market="US",
        cadence="weekly",
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        summary="US stocks rose as megacap tech earnings offset rate-cut worries.",
        bullets=[
            {
                "text": "Megacap tech led gains after stronger earnings.",
                "citations": [{"source_id": "src-1"}],
            },
            {
                "text": "Fed rate-cut expectations faded after sticky inflation data.",
                "citations": [{"source_id": "src-2"}],
            },
        ],
        sources=[
            {
                "id": "src-1",
                "url": "https://www.reuters.com/markets/us/tech-rally",
                "title": "Tech lifts Wall Street",
                "publisher": "reuters.com",
                "published_at": "2026-04-24T12:00:00Z",
                "fetched_at": "2026-04-25T08:00:00Z",
            },
            {
                "id": "src-2",
                "url": "https://www.wsj.com/markets/rates",
                "title": "Rate cut bets fade",
                "publisher": "wsj.com",
                "published_at": "2026-04-24T13:00:00Z",
                "fetched_at": "2026-04-25T08:00:00Z",
            },
        ],
        raw_sources={"candidates": [{"title": "Internal only"}]},
        questions=["What drove the rally?"],
        model="test-model",
    )


def test_recap_analyze_streams_recap_grounded_answer_and_sources():
    recap = _recap()

    async def fake_stream(**_kwargs):
        yield {"type": "conversation", "body": {"conversationId": "conv-recap"}}
        yield {"type": "answer", "body": "The recap says tech earnings offset rate worries."}
        yield {
            "type": "sources",
            "body": [
                {
                    "source_id": "src-1",
                    "url": "https://www.reuters.com/markets/us/tech-rally",
                    "title": "Tech lifts Wall Street",
                    "publisher": "reuters.com",
                    "published_at": "2026-04-24T12:00:00Z",
                    "is_trusted": True,
                }
            ],
        }
        yield {"type": "model_used", "body": "test-model"}

    with (
        patch("api.recap_analyze.recap_analyze_stream_service.get_recap", return_value=recap),
        patch("api.recap_analyze.recap_analyze_stream_service.stream", side_effect=fake_stream),
        TestClient(app) as client,
    ):
        with client.stream(
            "POST",
            f"/api/recaps/{recap.id}/analyze",
            json={"question": "What drove the market rally?", "conversationId": "conv-recap"},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert "anon_user_id=" in response.headers.get("set-cookie", "")
            body = response.read()

    events = _parse_sse(body)
    assert events[0] == {"type": "conversation", "body": {"conversationId": "conv-recap"}}
    assert events[1]["type"] == "answer"
    assert events[2]["type"] == "sources"
    assert "related_question" not in [event["type"] for event in events]


def test_recap_analyze_validates_question_and_recap_id():
    recap = _recap()

    with TestClient(app) as client:
        missing_question = client.post(f"/api/recaps/{recap.id}/analyze", json={})
        assert missing_question.status_code == 400

    with (
        patch("api.recap_analyze.recap_analyze_stream_service.get_recap", return_value=None),
        TestClient(app) as client,
    ):
        missing_recap = client.post("/api/recaps/999999/analyze", json={"question": "What happened?"})
        assert missing_recap.status_code == 404


def test_recap_analyze_uses_per_recap_conversation_scope():
    recap = _recap()
    captured = {}

    async def fake_stream(**kwargs):
        captured.update(kwargs)
        yield {"type": "conversation", "body": {"conversationId": "conv-existing"}}
        yield {"type": "answer", "body": "answer"}

    with (
        patch("api.recap_analyze.recap_analyze_stream_service.get_recap", return_value=recap),
        patch("api.recap_analyze.recap_analyze_stream_service.stream", side_effect=fake_stream),
        TestClient(app) as client,
    ):
        with client.stream(
            "POST",
            f"/api/recaps/{recap.id}/analyze",
            cookies={"anon_user_id": "anon-existing"},
            json={"question": "What about rates?", "conversationId": "conv-existing"},
        ) as response:
            assert response.status_code == 200
            assert "set-cookie" not in response.headers
            response.read()

    assert captured["recap"].id == recap.id
    assert captured["conversation_id"] == "conv-existing"
    assert captured["anon_user_id"] == "anon-existing"


def test_recap_analyze_translates_brave_failure_to_sse_error():
    recap = _recap()

    async def fake_stream(**_kwargs):
        yield {"type": "conversation", "body": {"conversationId": "conv-brave"}}
        raise BraveRetrievalError("no results")

    with (
        patch("api.recap_analyze.recap_analyze_stream_service.get_recap", return_value=recap),
        patch("api.recap_analyze.recap_analyze_stream_service.stream", side_effect=fake_stream),
        TestClient(app) as client,
    ):
        with client.stream(
            "POST",
            f"/api/recaps/{recap.id}/analyze",
            json={"question": "What changed after this recap?", "conversationId": "conv-brave"},
        ) as response:
            assert response.status_code == 200
            body = response.read()

    events = _parse_sse(body)
    assert events[0] == {"type": "conversation", "body": {"conversationId": "conv-brave"}}
    assert events[1]["type"] == "error"
    assert events[1]["code"] == "retrieval_failed"
