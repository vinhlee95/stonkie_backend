from __future__ import annotations

from datetime import UTC

from services.analyze_retrieval.schemas import AnalyzeSource


def _to_iso_z(source: AnalyzeSource) -> str | None:
    if source.published_at is None:
        return None
    return source.published_at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_sources_event(retrieved_sources: list[AnalyzeSource]) -> dict:
    """Emit all retrieved Brave sources as a footer 'sources' SSE event."""
    sources = [
        {
            "source_id": source.id,
            "url": source.url,
            "title": source.title,
            "publisher": source.publisher,
            "published_at": _to_iso_z(source),
            "is_trusted": source.is_trusted,
        }
        for source in retrieved_sources
    ]
    return {"type": "sources", "body": sources}
