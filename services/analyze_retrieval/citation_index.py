from __future__ import annotations

import json
import logging
import re
from datetime import UTC

from services.analyze_retrieval.schemas import AnalyzeSource

_CITATION_PATTERN = re.compile(r"\[(\d+)\]")
logger = logging.getLogger("app.analyze_retrieval")


def _to_iso_z(source: AnalyzeSource) -> str | None:
    if source.published_at is None:
        return None
    return source.published_at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_sources_event(full_text: str, retrieved_sources: list[AnalyzeSource]) -> dict:
    citation_numbers = [int(match) for match in _CITATION_PATTERN.findall(full_text)]
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
        "body": {"sources": sources},
    }
