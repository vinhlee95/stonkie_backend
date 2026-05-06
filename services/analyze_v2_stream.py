"""Layer-2 stream orchestration for the v2 analyze endpoint."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, AsyncGenerator

from ai_models.model_name import ModelName
from connectors.conversation_store import (
    append_assistant_message,
    append_user_message,
    generate_conversation_id,
    get_conversation_history_for_prompt,
)
from services.analyze_retrieval.schemas import BraveRetrievalError
from services.etf import get_etf_by_ticker
from services.financial_analyzer_v2 import FinancialAnalyzerV2
from services.semantic_analysis_cache import SemanticAnalysisCache
from utils.visual_stream import VisualAnswerStreamSplitter

logger = logging.getLogger(__name__)

DisconnectChecker = Callable[[], Awaitable[bool]]


def normalize_route_ticker(ticker: str) -> str:
    normalized = ticker.strip().upper() if ticker else ""
    if normalized in ["UNDEFINED", "NULL", ""]:
        return ""
    return normalized


def v2_cache_ticker(normalized_ticker: str) -> str:
    return f"v2:{normalized_ticker.strip().upper()}" if normalized_ticker.strip() else ""


class AnalyzeV2StreamService:
    def __init__(self, analyzer: FinancialAnalyzerV2) -> None:
        self._analyzer = analyzer

    async def stream(
        self,
        *,
        ticker: str,
        question: str,
        use_url_context: bool,
        deep_analysis: bool,
        preferred_model: ModelName,
        conversation_id: str | None,
        anon_user_id: str,
        is_disconnected: DisconnectChecker,
        cache_replay_request: Any,
        disable_cache: bool = False,
        debug_prompt_context: bool = False,
    ) -> AsyncGenerator[dict[str, Any], None]:
        normalized_ticker = normalize_route_ticker(ticker)

        is_etf = False
        if normalized_ticker and get_etf_by_ticker(normalized_ticker):
            is_etf = True

        conv_id = conversation_id or generate_conversation_id()
        storage_ticker = normalized_ticker or "none"
        if is_etf and normalized_ticker:
            storage_ticker = f"etf_{normalized_ticker}"

        conversation_messages = get_conversation_history_for_prompt(anon_user_id, storage_ticker, conv_id)

        append_user_message(anon_user_id, storage_ticker, conv_id, question)
        yield {"type": "conversation", "body": {"conversationId": conv_id}}

        if is_etf:
            yield {
                "type": "error",
                "code": "not_supported",
                "body": "ETF analysis is not supported on v2 yet",
            }
            return

        assistant_output_buffer: list[str] = []
        last_sources_payload: dict | list | None = None
        last_model_used: str | None = None
        last_related_questions: list[str] = []

        def append_assistant_output(event: dict[str, Any]) -> None:
            event_type = event.get("type")
            body = event.get("body", "")

            if event_type == "answer" and isinstance(body, str):
                assistant_output_buffer.append(body)
                return

            if event_type == "answer_visual_done" and isinstance(body, dict):
                lang = body.get("lang")
                content = body.get("content")
                if isinstance(lang, str) and isinstance(content, str):
                    assistant_output_buffer.append(f"```{lang}\n{content}```")

        def track_stream_meta(event: dict[str, Any]) -> None:
            nonlocal last_sources_payload, last_model_used, last_related_questions
            event_type = event.get("type")
            body = event.get("body")
            if event_type == "sources" and isinstance(body, (dict, list)):
                last_sources_payload = body
            elif event_type == "sources_grouped" and isinstance(body, dict):
                last_sources_payload = body
            elif event_type == "model_used" and isinstance(body, str):
                last_model_used = body
            elif event_type == "related_question" and isinstance(body, str) and body.strip():
                last_related_questions.append(body.strip())

        cache_ticker = v2_cache_ticker(normalized_ticker)
        use_semantic_cache = SemanticAnalysisCache.use_semantic_cache_enabled(
            cache_ticker,
            deep_analysis=deep_analysis,
            use_url_context=use_url_context,
            question=question,
        )
        if disable_cache:
            use_semantic_cache = False

        cached_entry = None
        if use_semantic_cache:
            cached_entry = await SemanticAnalysisCache.lookup_hit(cache_ticker, question)

        if cached_entry is not None:
            answer_text = cached_entry.answer_text or ""
            if answer_text.strip():
                append_assistant_message(anon_user_id, storage_ticker, conv_id, answer_text)
            async for event in SemanticAnalysisCache.stream_hit_replay(cache_replay_request, cached_entry):
                if await is_disconnected():
                    return
                yield event
            return

        visual_splitter = VisualAnswerStreamSplitter()
        try:
            analyzer_generator = self._analyzer.analyze_question(
                normalized_ticker or ticker,
                question,
                use_url_context=use_url_context,
                deep_analysis=deep_analysis,
                preferred_model=preferred_model,
                conversation_messages=conversation_messages,
                conversation_id=conv_id,
                anon_user_id=anon_user_id,
                debug_prompt_context=debug_prompt_context,
            )

            async for event in analyzer_generator:
                if await is_disconnected():
                    return

                if event.get("type") == "answer" and isinstance(event.get("body"), str):
                    for split_event in visual_splitter.process_text(event["body"]):
                        append_assistant_output(split_event)
                        yield split_event
                else:
                    append_assistant_output(event)
                    track_stream_meta(event)
                    yield event
        except BraveRetrievalError:
            logger.exception("v2 analyze retrieval failed", extra={"ticker": normalized_ticker or ticker})
            yield {"type": "error", "code": "retrieval_failed", "body": "Retrieval failed"}
            return

        for split_event in visual_splitter.finalize():
            append_assistant_output(split_event)
            yield split_event

        if assistant_output_buffer:
            assistant_full_text = "".join(assistant_output_buffer)
            append_assistant_message(anon_user_id, storage_ticker, conv_id, assistant_full_text)
            SemanticAnalysisCache.schedule_background_store(
                use_semantic_cache=use_semantic_cache,
                cache_ticker=cache_ticker,
                question=question,
                assistant_full_text=assistant_full_text,
                last_sources_payload=last_sources_payload,
                last_model_used=last_model_used,
                related_questions=last_related_questions or None,
                ttl_seconds=30 * 60,
            )
