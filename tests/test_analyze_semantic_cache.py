import asyncio
import json
from unittest.mock import ANY, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import main as main_module
import services.semantic_analysis_cache as sac_module


@pytest.fixture
def client():
    with TestClient(main_module.app) as c:
        yield c


def _parse_sse(raw: bytes) -> list[dict]:
    events: list[dict] = []
    for block in raw.decode("utf-8").strip().split("\n\n"):
        b = block.strip()
        if not b:
            continue
        events.append(json.loads(b))
    return events


def test_semantic_cache_hit_skips_analyzer(client):
    entry = MagicMock()
    entry.answer_text = "short reply"
    entry.sources = [{"name": "SEC", "url": "https://example.com"}]
    entry.model_used = "google/gemini-flash"

    async def analyzer_should_not_run(*_a, **_kw):
        raise AssertionError("analyzer should not run on cache hit")
        yield  # pragma: no cover

    with (
        patch.object(main_module, "append_user_message"),
        patch.object(main_module, "append_assistant_message") as append_asst,
        patch.object(main_module, "get_conversation_history_for_prompt", return_value=[]),
        patch.object(sac_module, "SemanticCache") as mock_sc,
        patch.object(main_module.financial_analyzer, "analyze_question", side_effect=analyzer_should_not_run),
        patch.object(main_module.etf_analyzer, "analyze_question", side_effect=analyzer_should_not_run),
        patch.object(main_module, "get_etf_by_ticker", return_value=None),
    ):
        inst = MagicMock()
        inst.embed.return_value = [0.01] * 1536
        inst.lookup.return_value = entry
        mock_sc.return_value = inst

        with client.stream(
            "POST",
            "/api/companies/AAPL/analyze",
            json={"question": "What is revenue?", "conversationId": "conv-test-1"},
        ) as response:
            assert response.status_code == 200
            body = response.read()

    events = _parse_sse(body)
    types = [e["type"] for e in events]
    assert "conversation" in types
    assert "thinking_status" in types
    assert types.count("answer") >= 1
    assert "sources" in types
    assert "cache_meta" in types
    meta = events[types.index("cache_meta")]["body"]
    assert meta.get("semantic_cache_hit") is True
    assert "model_used" in types
    append_asst.assert_called_once_with(ANY, "AAPL", "conv-test-1", "short reply")


def test_semantic_cache_hit_emits_answer_visual_for_fenced_html(client):
    """Cached replay must use VisualAnswerStreamSplitter so FE gets answer_visual_* not raw HTML in answer."""
    entry = MagicMock()
    entry.answer_text = 'Summary line.\n\n```html\n<div id="c">ok</div>\n```\n'
    entry.sources = None
    entry.model_used = "m"

    async def analyzer_should_not_run(*_a, **_kw):
        raise AssertionError("analyzer should not run on cache hit")
        yield  # pragma: no cover

    with (
        patch.object(main_module, "append_user_message"),
        patch.object(main_module, "append_assistant_message"),
        patch.object(main_module, "get_conversation_history_for_prompt", return_value=[]),
        patch.object(sac_module, "SemanticCache") as mock_sc,
        patch.object(main_module.financial_analyzer, "analyze_question", side_effect=analyzer_should_not_run),
        patch.object(main_module.etf_analyzer, "analyze_question", side_effect=analyzer_should_not_run),
        patch.object(main_module, "get_etf_by_ticker", return_value=None),
    ):
        inst = MagicMock()
        inst.embed.return_value = [0.01] * 1536
        inst.lookup.return_value = entry
        mock_sc.return_value = inst

        with client.stream(
            "POST",
            "/api/companies/AAPL/analyze",
            json={"question": "test?", "conversationId": "conv-vis-1"},
        ) as response:
            assert response.status_code == 200
            body = response.read()

    types = [e["type"] for e in _parse_sse(body)]
    assert "answer_visual_start" in types
    assert "answer_visual_delta" in types
    assert "answer_visual_done" in types


def test_semantic_cache_miss_runs_analyzer_and_schedules_store(client):
    async def fake_analyze(*_a, **_kw):
        yield {"type": "answer", "body": "live"}
        yield {"type": "model_used", "body": "m1"}

    captured: list = []

    def capture_task(coro):
        captured.append(coro)
        return MagicMock()

    with (
        patch.object(main_module, "append_user_message"),
        patch.object(main_module, "append_assistant_message"),
        patch.object(main_module, "get_conversation_history_for_prompt", return_value=[]),
        patch.object(sac_module, "SemanticCache") as mock_sc,
        patch.object(main_module.financial_analyzer, "analyze_question", side_effect=fake_analyze),
        patch.object(main_module, "get_etf_by_ticker", return_value=None),
        patch("services.semantic_analysis_cache.asyncio.create_task", side_effect=capture_task),
    ):
        inst = MagicMock()
        inst.embed.return_value = [0.02] * 1536
        inst.lookup.return_value = None
        mock_sc.return_value = inst

        with client.stream(
            "POST",
            "/api/companies/AAPL/analyze",
            json={"question": "What is margin?"},
        ) as response:
            assert response.status_code == 200
            response.read()

        assert len(captured) == 1
        asyncio.run(captured[0])
        inst.store.assert_called_once()
        args, _kwargs = inst.store.call_args
        assert args[0] == "AAPL"
        assert args[1] == "What is margin?"
        assert args[2] == "live"
        assert args[4] == "m1"


def test_semantic_cache_disabled_when_url_in_question(client):
    async def fake_analyze(*_a, **_kw):
        yield {"type": "answer", "body": "x"}
        yield {"type": "model_used", "body": "m"}

    with (
        patch.object(main_module, "append_user_message"),
        patch.object(main_module, "append_assistant_message"),
        patch.object(main_module, "get_conversation_history_for_prompt", return_value=[]),
        patch.object(sac_module, "SemanticCache") as mock_sc,
        patch.object(main_module.financial_analyzer, "analyze_question", side_effect=fake_analyze),
        patch.object(main_module, "get_etf_by_ticker", return_value=None),
        patch("services.semantic_analysis_cache.asyncio.create_task") as create_task,
    ):
        with client.stream(
            "POST",
            "/api/companies/AAPL/analyze",
            json={"question": "See https://example.com/doc.pdf for context"},
        ) as response:
            assert response.status_code == 200
            response.read()

    mock_sc.assert_not_called()
    create_task.assert_not_called()
