import asyncio
from unittest.mock import AsyncMock, MagicMock

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
