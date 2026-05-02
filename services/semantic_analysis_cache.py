"""Semantic analysis cache: eligibility, lookup, hit replay, and miss-path background store only."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import Request

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from ai_models.openrouter_client import get_openrouter_model_name
from connectors.semantic_cache import SemanticCache
from models.semantic_cache import SemanticCacheEntry
from services.analysis_progress import AnalysisPhase, thinking_status
from services.question_analyzer.context_builders.components import PromptComponents
from utils.url_helper import extract_first_url
from utils.visual_stream import VisualAnswerStreamSplitter

logger = logging.getLogger(__name__)

_CACHE_REPLAY_PACE_DEFAULT_SEC = 0.01
_CACHE_REPLAY_PACE_VISUAL_DELTA_SEC = 0.004


def _legacy_related_prompt(original_question: str) -> str:
    """Same shape as BaseQuestionHandler._generate_related_questions for non-ETF flows."""
    return f"""
                {PromptComponents.current_date()}

                Based on this original question: "{original_question}"

                Generate exactly 3 high-quality follow-up questions that a curious investor might naturally ask next.

                Requirements:
                - Each question should explore a DIFFERENT dimension:
                * Question 1: Go deeper into the same topic (more specific/detailed)
                * Question 2: Compare or contrast with a related concept, company, or time period
                * Question 3: Explore a related but adjacent topic (e.g., if original was about revenue, ask about profitability or cash flow)
                - Keep questions between 8-15 words
                - Make them actionable and specific (avoid vague questions like "What else should I know?")
                - Frame questions naturally, as a user would ask them
                - Ensure questions are relevant to the original context (financial analysis, company performance, market trends)
                - Do NOT number the questions or add any prefixes
                - Put EACH question on its OWN LINE

                Output format (one question per line):
                How does Apple's gross margin compare to its competitors?
                What was the main driver behind revenue growth last quarter?
                Is the current valuation sustainable given industry trends?
            """


def _resolve_model_for_related(stored: str | None) -> ModelName:
    """Map stored OpenRouter model string back to ModelName for MultiAgent."""
    if not stored or stored == "unknown":
        return ModelName.Fastest
    for m in ModelName:
        if get_openrouter_model_name(m) == stored:
            return m
    return ModelName.Fastest


def _normalize_stored_related_questions(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


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
    async def _stream_legacy_related_questions(
        request: Request,
        cached_entry: SemanticCacheEntry,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Best-effort regeneration for cache rows created before related_questions was stored."""

        def _sync_lines() -> list[str]:
            original_question = cached_entry.question_text or ""
            model = _resolve_model_for_related(cached_entry.model_used)
            agent = MultiAgent(model_name=model)
            return list(
                agent.generate_content_by_lines(
                    prompt=_legacy_related_prompt(original_question),
                    use_google_search=False,
                    max_lines=3,
                    min_line_length=10,
                    strip_numbering=True,
                    strip_markdown=True,
                )
            )

        try:
            lines = await asyncio.to_thread(_sync_lines)
        except Exception:
            logger.exception("Legacy related-questions regeneration failed")
            return

        for line in lines:
            if await request.is_disconnected():
                return
            yield {"type": "related_question", "body": line}
            await asyncio.sleep(_CACHE_REPLAY_PACE_DEFAULT_SEC)

    @staticmethod
    async def stream_hit_replay(
        request: Request,
        cached_entry: SemanticCacheEntry,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """SSE dicts from cache-notice through model_used and related_question (caller emits conversation first)."""

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
        await asyncio.sleep(_CACHE_REPLAY_PACE_DEFAULT_SEC)

        stored_rq = _normalize_stored_related_questions(getattr(cached_entry, "related_questions", None))
        if stored_rq:
            for rq in stored_rq:
                if await request.is_disconnected():
                    return
                yield {"type": "related_question", "body": rq}
                await asyncio.sleep(_CACHE_REPLAY_PACE_DEFAULT_SEC)
        else:
            async for rel_ev in SemanticAnalysisCache._stream_legacy_related_questions(request, cached_entry):
                yield rel_ev

    @staticmethod
    def schedule_background_store(
        *,
        use_semantic_cache: bool,
        cache_ticker: str,
        question: str,
        assistant_full_text: str,
        last_sources_payload: dict | list | None,
        last_model_used: str | None,
        related_questions: list[str] | None = None,
        ttl_seconds: int | None = None,
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
                        related_questions=related_questions,
                        ttl_seconds=ttl_seconds,
                    )

                await asyncio.to_thread(_do_store)
            except Exception:
                logger.exception("Semantic cache background store failed")

        asyncio.create_task(_store_semantic_cache_background())
