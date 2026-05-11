"""Streaming analysis service for market recap chat."""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Literal

from langfuse import observe
from langfuse._client.get_client import get_client as get_langfuse_client

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.brave_client import BraveClient
from connectors.conversation_store import (
    append_assistant_message,
    append_user_message,
    generate_conversation_id,
    get_conversation_history_for_prompt,
)
from connectors.market_recap import MarketRecapConnector, MarketRecapDto
from services.analysis_progress import AnalysisPhase, thinking_status
from services.analyze_retrieval.citation_index import build_sources_event
from services.analyze_retrieval.retrieval import retrieve_for_analyze
from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeSource
from services.analyze_retrieval.source_policy import Market, is_trusted
from services.market_recap.url_utils import source_id_for
from utils.visual_stream import VisualAnswerStreamSplitter

logger = logging.getLogger(__name__)

RecapRoute = Literal["recap_related", "market_search", "unrelated_nonfinance"]


@dataclass(frozen=True)
class RecapRelevanceDecision:
    route: RecapRoute
    reason: str


def recap_conversation_scope(recap_id: int) -> str:
    return f"recap:{recap_id}"


def _clean_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _json_block(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("JSON block is not an object")
    return parsed


def _market_for_recap(recap: MarketRecapDto) -> Market:
    market = (recap.market or "").upper()
    if market == "VN":
        return "VN"
    if market == "FI":
        return "FI"
    return "GLOBAL"


def _source_datetime_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, str):
        return value
    return None


def _recap_sources(recap: MarketRecapDto) -> list[AnalyzeSource]:
    sources: list[AnalyzeSource] = []
    for raw in recap.sources or []:
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or "")
        source_id = str(raw.get("id") or source_id_for(url))
        sources.append(
            AnalyzeSource(
                id=source_id,
                url=url,
                title=str(raw.get("title") or ""),
                publisher=str(raw.get("publisher") or ""),
                published_at=raw.get("published_at"),
                is_trusted=is_trusted(url, _market_for_recap(recap)),
                raw_content="",
            )
        )
    return sources


def _build_recap_context(recap: MarketRecapDto) -> str:
    lines = [
        "Recap context:",
        f"- Market: {recap.market}",
        f"- Cadence: {recap.cadence}",
        f"- Period: {recap.period_start.isoformat()} to {recap.period_end.isoformat()}",
        f"- Summary: {_clean_whitespace(recap.summary or '')}",
        "",
        "Key recap bullets:",
    ]
    for index, bullet in enumerate(recap.bullets or [], start=1):
        if not isinstance(bullet, dict):
            continue
        citations = []
        for citation in bullet.get("citations") or []:
            if isinstance(citation, dict) and citation.get("source_id"):
                citations.append(str(citation["source_id"]))
        citation_suffix = f" [sources: {', '.join(citations)}]" if citations else ""
        lines.append(f"{index}. {_clean_whitespace(str(bullet.get('text') or ''))}{citation_suffix}")

    lines.extend(["", "Recap sources:"])
    for source in recap.sources or []:
        if not isinstance(source, dict):
            continue
        published = _source_datetime_to_iso(source.get("published_at")) or "unknown date"
        lines.append(
            "- "
            f"{source.get('id')}: {source.get('title')} "
            f"({source.get('publisher')}, {published}) {source.get('url')}"
        )
    return "\n".join(lines)


def _format_conversation(messages: list[dict[str, str]] | None) -> str:
    if not messages:
        return ""
    lines = []
    for msg in messages[-6:]:
        role = (msg.get("role") or "").upper()
        content = _clean_whitespace(msg.get("content") or "")
        if role and content:
            lines.append(f"{role}: {content}")
    if not lines:
        return ""
    return "Recent conversation:\n" + "\n".join(lines)


def _build_search_query(question: str, recap: MarketRecapDto) -> str:
    stopwords = {
        "after",
        "amid",
        "before",
        "from",
        "market",
        "markets",
        "near",
        "recap",
        "respectively",
        "since",
        "their",
        "this",
        "week",
        "weekly",
        "with",
    }
    topic_words: list[str] = []
    topic_texts = [str(recap.summary or "")]
    for bullet in recap.bullets or []:
        if isinstance(bullet, dict) and bullet.get("text"):
            topic_texts.append(str(bullet["text"]))
    for word in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", " ".join(topic_texts)):
        normalized = word.lower()
        if normalized in stopwords or normalized in topic_words:
            continue
        topic_words.append(normalized)
        if len(topic_words) >= 10:
            break
    topic_text = " ".join(topic_words)
    after_context = ""
    if _asks_after_recap(question):
        after_context = f"after {recap.period_end.strftime('%B %-d %Y')} latest"
    return _clean_whitespace(
        " ".join(
            [
                question,
                recap.market or "",
                recap.cadence or "",
                after_context,
                "recap period",
                recap.period_start.isoformat(),
                recap.period_end.isoformat(),
                topic_text,
            ]
        )
    )[:240]


def _asks_after_recap(question: str) -> bool:
    return bool(re.search(r"\b(after|since|following|changed|new|latest|update|updated)\b", question, re.IGNORECASE))


def _filter_post_recap_sources(
    *,
    recap: MarketRecapDto,
    question: str,
    sources: list[AnalyzeSource],
    selected_passages: list[AnalyzePassage],
) -> tuple[list[AnalyzeSource], list[AnalyzePassage]]:
    if not _asks_after_recap(question):
        return sources, selected_passages
    filtered_sources = [
        source for source in sources if source.published_at is None or source.published_at.date() >= recap.period_end
    ]
    kept_ids = {source.id for source in filtered_sources}
    filtered_passages = [passage for passage in selected_passages if passage.source_id in kept_ids]
    return filtered_sources, filtered_passages


def _sources_thinking_status(sources: list[AnalyzeSource]) -> dict[str, Any] | None:
    if not sources:
        return None
    publishers: list[str] = []
    for source in sources:
        publisher = (source.publisher or "").strip()
        if publisher and publisher not in publishers:
            publishers.append(publisher)
    suffix = f": {', '.join(publishers)}" if publishers else ""
    return thinking_status(
        f"Reading {len(sources)} sources{suffix}",
        phase=AnalysisPhase.SEARCH,
        step=2,
        total_steps=3,
    )


def _build_answer_prompt(
    question: str,
    recap_context: str,
    conversation_context: str,
    *,
    external_context: str = "",
) -> str:
    search_rules = ""
    if external_context:
        search_rules = """
External search context:
{external_context}

Additional rules for external search:
- Use the search context for facts not present in the recap.
- Prefer recent, specific evidence over generic market knowledge.
- If the search context does not answer the question, say that clearly.
- You must connect the external information back to the recap by explaining whether it changes, supports, or contrasts with the recap.
""".format(external_context=external_context)

    return f"""
You are a financial analyst helping a user discuss a specific market recap.

Answer the user's current question using the provided recap context first. This chat is about one specific market recap, so preserve that framing in your answer.

Current question:
{question}

{recap_context}

{conversation_context}

Rules:
- Answer in the same language as the current question.
- If the question is recap-related, answer only from the recap context above.
- If Brave search context is provided, use it for fresh or adjacent market context and explicitly connect the answer back to the recap.
- Do not invent facts, figures, or URLs.
- Do not include inline source URLs or a "Sources:" section in the answer text.
- Keep the answer under 150 words unless the user explicitly asks for a deeper breakdown.
- Start with the direct answer. Avoid setup phrases and recap restatement.
- Use short paragraphs or up to 4 bullets for readability.
- Include only the most decision-useful facts and numbers.

{search_rules}
    """.strip()


def _build_sources_block(
    retrieval_sources: list[AnalyzeSource],
    selected_passages: list[AnalyzePassage] | None = None,
) -> str:
    if not retrieval_sources:
        return ""
    passages_by_source_id: dict[str, list[AnalyzePassage]] = {}
    for passage in selected_passages or []:
        passages_by_source_id.setdefault(passage.source_id, []).append(passage)
    blocks = []
    for index, source in enumerate(retrieval_sources, start=1):
        published = source.published_at.isoformat() if source.published_at else "unknown date"
        content_lines = [
            f"Passage [{passage.passage_index}]: {passage.content}"
            for passage in passages_by_source_id.get(source.id, [])
        ]
        if not content_lines and source.raw_content:
            content_lines = [f"Content: {source.raw_content[:1500]}"]
        blocks.append(
            "\n".join(
                [
                    f"Source [{index}]",
                    f"Title: {source.title}",
                    f"Publisher: {source.publisher}",
                    f"Published: {published}",
                    f"URL: {source.url}",
                    *content_lines,
                ]
            )
        )
    return "\n\n".join(blocks)


def _extract_answer_text(chunks: list) -> str:
    return "".join(c.get("body", "") for c in chunks if isinstance(c, dict) and c.get("type") == "answer")


class RecapAnalyzeStreamService:
    def __init__(self, recap_connector: MarketRecapConnector | None = None) -> None:
        self._recap_connector = recap_connector or MarketRecapConnector()

    def get_recap(self, recap_id: int) -> MarketRecapDto | None:
        return self._recap_connector.get_by_id(recap_id)

    @observe(name="recap_classify_relevance")
    def _classify_relevance(
        self,
        *,
        question: str,
        recap_context: str,
        conversation_context: str,
        preferred_model: ModelName,
    ) -> RecapRelevanceDecision:
        if _asks_after_recap(question):
            return RecapRelevanceDecision(
                route="market_search", reason="question asks for post-recap or updated context"
            )

        prompt = f"""
You are a strict JSON classifier for a market recap chat.

Classify the user's current question into exactly one route:
- recap_related: the question can be answered from the recap context and recent conversation.
- market_search: the question is about markets, companies, sectors, macro, or events but needs current/outside context.
- unrelated_nonfinance: the question is not about the recap, markets, finance, companies, or investing.

Current question:
{question}

{recap_context}

{conversation_context}

Output ONLY JSON:
{{"route":"recap_related|market_search|unrelated_nonfinance","reason":"short reason"}}
        """.strip()

        try:
            agent = MultiAgent(model_name=preferred_model)
            raw = "".join(
                chunk
                for chunk in agent.generate_content(prompt=prompt, use_google_search=False)
                if isinstance(chunk, str)
            )
            parsed = _json_block(raw)
            route = parsed.get("route")
            if route == "recap_related":
                return RecapRelevanceDecision(route="recap_related", reason=str(parsed.get("reason") or ""))
            if route in {"adjacent_needs_search", "market_general_needs_search", "market_search"}:
                return RecapRelevanceDecision(route="market_search", reason=str(parsed.get("reason") or ""))
            if route == "unrelated_nonfinance":
                return RecapRelevanceDecision(route="unrelated_nonfinance", reason=str(parsed.get("reason") or ""))
        except Exception:
            logger.exception("Recap relevance classification failed; falling back to recap context")
        return RecapRelevanceDecision(route="recap_related", reason="fallback")

    @observe(
        name="recap_analyze.stream",
        as_type="generation",
        capture_input=False,
        transform_to_string=_extract_answer_text,
    )
    async def stream(
        self,
        *,
        recap: MarketRecapDto,
        question: str,
        preferred_model: ModelName,
        conversation_id: str | None,
        anon_user_id: str,
        is_disconnected,
        debug_prompt_context: bool = False,
    ) -> AsyncGenerator[dict[str, Any], None]:
        langfuse = get_langfuse_client()
        if langfuse:
            langfuse.update_current_generation(input=question)
        ttft_recorded = False

        conv_id = conversation_id or generate_conversation_id()
        storage_scope = recap_conversation_scope(recap.id)
        conversation_messages = get_conversation_history_for_prompt(anon_user_id, storage_scope, conv_id)

        append_user_message(anon_user_id, storage_scope, conv_id, question)
        yield {"type": "conversation", "body": {"conversationId": conv_id}}

        recap_context = _build_recap_context(recap)
        conversation_context = _format_conversation(conversation_messages)
        yield thinking_status("Reading the recap context...", phase=AnalysisPhase.ANALYZE, step=1, total_steps=3)
        decision = self._classify_relevance(
            question=question,
            recap_context=recap_context,
            conversation_context=conversation_context,
            preferred_model=preferred_model,
        )

        if decision.route == "unrelated_nonfinance":
            body = (
                "This chat is focused on the selected market recap and related market questions. "
                "Ask me about the recap, the market moves, sectors, companies, macro drivers, or what changed since the recap."
            )
            yield {"type": "answer", "body": body}
            append_assistant_message(anon_user_id, storage_scope, conv_id, body)
            return

        retrieved_sources: list[AnalyzeSource] = []
        external_context = ""
        if decision.route == "market_search":
            yield thinking_status(
                "Searching for updated market context...", phase=AnalysisPhase.SEARCH, step=2, total_steps=3
            )
            brave_client = BraveClient(api_key=os.getenv("BRAVE_API_KEY", ""))
            retrieval_result = retrieve_for_analyze(
                question=_build_search_query(question, recap),
                market=_market_for_recap(recap),
                request_id=str(uuid.uuid4()),
                brave_client=brave_client,
                ticker=None,
                company_name=None,
            )
            retrieved_sources, selected_passages = _filter_post_recap_sources(
                recap=recap,
                question=question,
                sources=retrieval_result.sources,
                selected_passages=retrieval_result.selected_passages,
            )
            source_status = _sources_thinking_status(retrieved_sources)
            if source_status is not None:
                yield source_status
            external_context = _build_sources_block(retrieved_sources, selected_passages)
        else:
            yield thinking_status("Answering from the recap...", phase=AnalysisPhase.ANALYZE, step=2, total_steps=3)

        prompt = _build_answer_prompt(
            question,
            recap_context,
            conversation_context,
            external_context=external_context,
        )
        if debug_prompt_context:
            yield {
                "type": "debug_prompt_context",
                "body": {
                    "handler": "recap_analyze",
                    "gate": {"route": decision.route, "reason": decision.reason},
                    "prompt": prompt,
                    "source_count": len(retrieved_sources),
                },
            }

        yield thinking_status("Writing your answer...", phase=AnalysisPhase.ANALYZE, step=3, total_steps=3)
        assistant_output_buffer: list[str] = []
        visual_splitter = VisualAnswerStreamSplitter()
        agent = MultiAgent(model_name=preferred_model)
        for chunk in agent.generate_content(prompt=prompt, use_google_search=False):
            if await is_disconnected():
                return
            if not isinstance(chunk, str):
                continue
            for split_event in visual_splitter.process_text(chunk):
                if split_event.get("type") == "answer" and isinstance(split_event.get("body"), str):
                    assistant_output_buffer.append(split_event["body"])
                    if not ttft_recorded and langfuse:
                        langfuse.update_current_generation(completion_start_time=datetime.datetime.now())
                        ttft_recorded = True
                yield split_event

        for split_event in visual_splitter.finalize():
            if split_event.get("type") == "answer" and isinstance(split_event.get("body"), str):
                assistant_output_buffer.append(split_event["body"])
            yield split_event

        if decision.route == "market_search":
            yield build_sources_event(retrieved_sources)
        else:
            yield build_sources_event(_recap_sources(recap))

        yield {"type": "model_used", "body": agent.model_name}

        if assistant_output_buffer:
            append_assistant_message(anon_user_id, storage_scope, conv_id, "".join(assistant_output_buffer))
