from __future__ import annotations

import json
import logging
import re
from datetime import UTC
from typing import Final

from services.analyze_retrieval.schemas import AnalyzeSource

_CITATION_PATTERN = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_TRAILING_INCOMPLETE_CITATION_PATTERN: Final[re.Pattern[str]] = re.compile(r"\[[\d,\s]*$")
_SPACE_BEFORE_PUNCTUATION_PATTERN: Final[re.Pattern[str]] = re.compile(r"\s+([.,;:!?])")
logger = logging.getLogger("app.analyze_retrieval")


def _to_iso_z(source: AnalyzeSource) -> str | None:
    if source.published_at is None:
        return None
    return source.published_at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def strip_citation_markers(text: str) -> str:
    """Remove inline [N] / [N, M] markers from answer text."""
    stripped = _CITATION_PATTERN.sub("", text)
    stripped = re.sub(r"[ \t]{2,}", " ", stripped)
    return _SPACE_BEFORE_PUNCTUATION_PATTERN.sub(r"\1", stripped)


class CitationTextStreamCleaner:
    """Strip inline citation markers while tolerating chunk boundaries."""

    def __init__(self) -> None:
        self._buffer = ""
        self._pending_space = False

    def process(self, chunk: str) -> str:
        self._buffer += chunk
        hold_from = self._incomplete_citation_start(self._buffer)
        if hold_from is None:
            safe_text = self._buffer
            self._buffer = ""
        else:
            safe_text = self._buffer[:hold_from]
            self._buffer = self._buffer[hold_from:]
        return self._normalize_chunk(strip_citation_markers(safe_text))

    def finalize(self) -> str:
        if not self._buffer:
            return ""
        safe_text = self._buffer
        self._buffer = ""
        return self._normalize_chunk(strip_citation_markers(safe_text), final=True)

    @staticmethod
    def _incomplete_citation_start(text: str) -> int | None:
        match = _TRAILING_INCOMPLETE_CITATION_PATTERN.search(text)
        if match is None:
            return None
        return match.start()

    def _normalize_chunk(self, text: str, *, final: bool = False) -> str:
        if not text:
            return ""

        stripped = text.strip(" \t")
        if not stripped:
            self._pending_space = self._pending_space or (not final and bool(text))
            return ""

        prefix = " " if self._pending_space else ""
        trailing_space = bool(text[-1:].isspace())
        self._pending_space = trailing_space and not final
        return prefix + stripped


def build_sources_event(full_text: str, retrieved_sources: list[AnalyzeSource]) -> dict:
    citation_numbers: list[int] = []
    for match in _CITATION_PATTERN.findall(full_text):
        for part in match.split(","):
            citation_numbers.append(int(part.strip()))
    cited_indices: list[int] = []
    seen: set[int] = set()
    out_of_range_citation_count = 0

    for number in citation_numbers:
        source_idx = number - 1
        if source_idx < 0 or source_idx >= len(retrieved_sources):
            out_of_range_citation_count += 1
            continue
        if source_idx in seen:
            continue
        seen.add(source_idx)
        cited_indices.append(source_idx)

    uncited_source_count = max(len(retrieved_sources) - len(cited_indices), 0)
    logger.info(
        json.dumps(
            {
                "out_of_range_citation_count": out_of_range_citation_count,
                "uncited_source_count": uncited_source_count,
            },
            sort_keys=True,
        )
    )

    sources = []
    for source_idx in cited_indices:
        source = retrieved_sources[source_idx]
        sources.append(
            {
                "source_id": source.id,
                "url": source.url,
                "title": source.title,
                "publisher": source.publisher,
                "published_at": _to_iso_z(source),
                "is_trusted": source.is_trusted,
            }
        )

    return {
        "type": "sources",
        "body": sources,
    }
