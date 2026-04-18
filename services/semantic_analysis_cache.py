"""Semantic analysis cache: eligibility, lookup, hit replay, and miss-path background store only."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import Request

from connectors.semantic_cache import SemanticCache
from models.semantic_cache import SemanticCacheEntry
from services.analysis_progress import AnalysisPhase, thinking_status
from utils.url_helper import extract_first_url
from utils.visual_stream import VisualAnswerStreamSplitter

logger = logging.getLogger(__name__)

_CACHE_REPLAY_PACE_DEFAULT_SEC = 0.01
_CACHE_REPLAY_PACE_VISUAL_DELTA_SEC = 0.004


class SemanticAnalysisCache:
    """No analyzer dependencies — cache hit/miss + replay + optional background store only."""

    @staticmethod
    def use_semantic_cache_enabled(
        cache_ticker: str,
        *,
        deep_analysis: bool,
        use_url_context: bool,
        question: str,
    ) -> bool:
        return bool(cache_ticker) and not deep_analysis and not use_url_context and extract_first_url(question) is None

    @staticmethod
    async def lookup_hit(cache_ticker: str, question: str) -> Optional[SemanticCacheEntry]:
        try:
            sc = SemanticCache()
            embedding = await asyncio.to_thread(sc.embed, question)
            return await asyncio.to_thread(sc.lookup, cache_ticker, embedding)
        except Exception:
            logger.exception("Semantic cache lookup failed; continuing with live pipeline")
            return None

    @staticmethod
    async def stream_hit_replay(
        request: Request,
        cached_entry: SemanticCacheEntry,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """SSE dicts from cache-notice through model_used (caller emits conversation first)."""

        async def _pace_after_cache_event(ev: dict) -> None:
            delay = (
                _CACHE_REPLAY_PACE_VISUAL_DELTA_SEC
                if ev.get("type") == "answer_visual_delta"
                else _CACHE_REPLAY_PACE_DEFAULT_SEC
            )
            await asyncio.sleep(delay)

        yield thinking_status(
            "Serving cached answer...",
            phase=AnalysisPhase.ANALYZE,
            step=1,
            total_steps=1,
        )
        await asyncio.sleep(_CACHE_REPLAY_PACE_DEFAULT_SEC)

        answer_text = cached_entry.answer_text or ""
        visual_splitter = VisualAnswerStreamSplitter()
        for visual_event in visual_splitter.process_text(answer_text):
            if await request.is_disconnected():
                return
            yield visual_event
            await _pace_after_cache_event(visual_event)
        for visual_event in visual_splitter.finalize():
            if await request.is_disconnected():
                return
            yield visual_event
            await _pace_after_cache_event(visual_event)

        raw_sources = cached_entry.sources
        sources_out = None
        if isinstance(raw_sources, dict) and isinstance(raw_sources.get("sources"), list):
            sources_out = raw_sources["sources"] or None
        elif isinstance(raw_sources, list) and raw_sources:
            sources_out = raw_sources
        elif isinstance(raw_sources, dict):
            sources_out = raw_sources
        if sources_out:
            yield {"type": "sources", "body": sources_out}
            await asyncio.sleep(_CACHE_REPLAY_PACE_DEFAULT_SEC)

        yield {"type": "cache_meta", "body": {"semantic_cache_hit": True}}
        await asyncio.sleep(_CACHE_REPLAY_PACE_DEFAULT_SEC)
        yield {"type": "model_used", "body": cached_entry.model_used or "unknown"}

    @staticmethod
    def schedule_background_store(
        *,
        use_semantic_cache: bool,
        cache_ticker: str,
        question: str,
        assistant_full_text: str,
        last_sources_payload: dict | list | None,
        last_model_used: str | None,
    ) -> None:
        if not (use_semantic_cache and assistant_full_text.strip()):
            return

        async def _store_semantic_cache_background() -> None:
            try:

                def _do_store() -> None:
                    sc2 = SemanticCache()
                    emb2 = sc2.embed(question)
                    sc2.store(
                        cache_ticker,
                        question,
                        assistant_full_text,
                        last_sources_payload,
                        last_model_used or "unknown",
                        emb2,
                    )

                await asyncio.to_thread(_do_store)
            except Exception:
                logger.exception("Semantic cache background store failed")

        asyncio.create_task(_store_semantic_cache_background())
