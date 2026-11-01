"""The answer model's web_search tool: more Brave retrieval when the prompt's sources don't explain a move."""

from __future__ import annotations

import logging
import threading
from typing import Callable

from ai_models.chat_tool import ChatTool, ChatToolResult
from services.analyze_retrieval.relevance import merge_sources
from services.analyze_retrieval.schemas import (
    AnalyzePassage,
    AnalyzeRetrievalResult,
    AnalyzeSource,
    BraveRetrievalError,
)

logger = logging.getLogger(__name__)

WEB_SEARCH_TOOL_NAME = "web_search"
_NO_RESULTS = "No relevant results."
_MAX_STATUS_PUBLISHERS = 3


def build_sources_block(
    retrieved_sources,
    selected_passages: list[AnalyzePassage] | None = None,
    *,
    use_excerpt_labels: bool = False,
) -> str:
    if not retrieved_sources:
        return ""
    passages_by_source_id: dict[str, list[AnalyzePassage]] = {}
    for passage in selected_passages or []:
        passages_by_source_id.setdefault(passage.source_id, []).append(passage)
    blocks = []
    for idx, source in enumerate(retrieved_sources, start=1):
        published = source.published_at.isoformat() if source.published_at else "null"
        passage_lines = []
        for passage in passages_by_source_id.get(source.id, []):
            if use_excerpt_labels:
                passage_lines.append(f"Extracted paragraph: {passage.content}")
            else:
                passage_lines.append(f"Passage {passage.passage_index}: {passage.content}")
        if not passage_lines:
            fallback_content = (source.raw_content or "").strip()
            if fallback_content:
                passage_lines.append(f"Content: {fallback_content}")
        block_lines = [
            f"Document {idx}" if use_excerpt_labels else f"Source {idx}",
            f"Title: {source.title}",
            f"URL: {source.url}",
            f"Published: {published}",
            *passage_lines,
        ]
        blocks.append("\n".join(block_lines))
    block_header = "Extracted document context" if use_excerpt_labels else "Sources"
    return f"\n\n{block_header}:\n" + "\n\n".join(blocks)


class WebSearchTool:
    """Wraps a blocking `search(query)` as the `web_search` ChatTool and keeps what it found for the sources footer.

    `run` executes on the tool loop's worker threads, so the accumulated results are guarded by a lock.
    """

    def __init__(
        self,
        search: Callable[[str], AnalyzeRetrievalResult],
        known_sources: list[AnalyzeSource] | None = None,
    ):
        self._search = search
        self._lock = threading.Lock()
        self._seen_ids = {s.id for s in known_sources or []}
        self._results: list[tuple[list[AnalyzeSource], list[AnalyzePassage]]] = []

    def chat_tool(self) -> ChatTool:
        return ChatTool(
            name=WEB_SEARCH_TOOL_NAME,
            description=(
                "Search recent web news and analysis. Use it to find the cause of a notable move in the data "
                "(a jump in a line item, a margin step change, a one-off gain or charge) that the provided "
                "Sources do not explain. Write a specific query: company name, period and item."
            ),
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search-engine keywords, under 15 words"}},
                "required": ["query"],
            },
            run=self._run,
        )

    def _run(self, arguments: dict) -> ChatToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return ChatToolResult(_NO_RESULTS, {"source_count": 0, "publishers": []})
        try:
            result = self._search(query)
        except BraveRetrievalError:
            logger.info("web_search tool found nothing relevant", extra={"query": query})
            return ChatToolResult(_NO_RESULTS, {"source_count": 0, "publishers": []})
        with self._lock:
            new_sources = [s for s in result.sources if s.id not in self._seen_ids]
            self._seen_ids.update(s.id for s in new_sources)
            self._results.append((result.sources, result.selected_passages))
        publishers: list[str] = []
        for source in new_sources:
            if source.is_trusted and source.publisher not in publishers:
                publishers.append(source.publisher)
        content = build_sources_block(result.sources, result.selected_passages).strip() or _NO_RESULTS
        return ChatToolResult(
            content, {"source_count": len(new_sources), "publishers": publishers[:_MAX_STATUS_PUBLISHERS]}
        )

    def merged_sources(
        self, initial_sources: list[AnalyzeSource], initial_passages: list[AnalyzePassage]
    ) -> tuple[list[AnalyzeSource], list[AnalyzePassage]]:
        """Prompt sources first, then everything the tool found, without repeats."""
        with self._lock:
            results = list(self._results)
        return merge_sources([(initial_sources, initial_passages), *results])
