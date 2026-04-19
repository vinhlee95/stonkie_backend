import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from ai_models.model_name import ModelName
from ai_models.openrouter_client import get_openrouter_model_name
from services.semantic_analysis_cache import SemanticAnalysisCache


def test_stream_hit_replay_splits_visual_blocks():
    """Cached answer_text is replayed through VisualAnswerStreamSplitter (no analyzer deps)."""

    async def _run() -> list[dict]:
        request = MagicMock()
        request.is_disconnected = AsyncMock(return_value=False)

        entry = MagicMock()
        entry.answer_text = "Intro\n\n```html\n<div>x</div>\n```\n"
        entry.sources = None
        entry.model_used = "model-x"
        entry.related_questions = ["One?", "Two?", "Three?"]

        out: list[dict] = []
        async for ev in SemanticAnalysisCache.stream_hit_replay(request, entry):
            out.append(ev)
        return out

    events = asyncio.run(_run())
    types = [e["type"] for e in events]
    assert "thinking_status" in types
    assert "answer_visual_start" in types
    assert "answer_visual_done" in types
    assert "cache_meta" in types
    assert events[types.index("cache_meta")]["body"].get("semantic_cache_hit") is True
    assert types.count("related_question") == 3
    mu_idx = types.index("model_used")
    assert types.index("related_question") > mu_idx


def test_stream_hit_replay_legacy_related_when_not_stored():
    """Rows without related_questions trigger MultiAgent fallback (mocked)."""

    async def _run() -> list[dict]:
        request = MagicMock()
        request.is_disconnected = AsyncMock(return_value=False)

        entry = MagicMock()
        entry.answer_text = "Short cached answer."
        entry.sources = None
        entry.model_used = get_openrouter_model_name(ModelName.Fastest)
        entry.question_text = "What is margin?"
        entry.related_questions = None

        out: list[dict] = []
        mock_agent = MagicMock()
        mock_agent.generate_content_by_lines.return_value = iter(
            ["Legacy related question one?", "Legacy related question two?", "Legacy related question three?"]
        )
        with patch("services.semantic_analysis_cache.MultiAgent", return_value=mock_agent):
            async for ev in SemanticAnalysisCache.stream_hit_replay(request, entry):
                out.append(ev)
        return out

    events = asyncio.run(_run())
    types = [e["type"] for e in events]
    assert types.count("related_question") == 3
    bodies = [e["body"] for e in events if e["type"] == "related_question"]
    assert bodies[0].startswith("Legacy related")
