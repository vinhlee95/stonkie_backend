"""Format retrieved Brave sources and passages as a prompt block."""

from collections.abc import Callable

from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeSource


def build_sources_block(
    sources: list[AnalyzeSource],
    passages: list[AnalyzePassage] | None = None,
    *,
    include_url: bool = True,
    clean: Callable[[str], str] = lambda text: text,
) -> str:
    """One "Source [n]" block per source with its selected passages (or the start of its raw content).
    `clean` is applied to every piece of web text, e.g. to strip prompt-fence tags."""
    if not sources:
        return ""
    by_source: dict[str, list[AnalyzePassage]] = {}
    for passage in passages or []:
        by_source.setdefault(passage.source_id, []).append(passage)
    blocks = []
    for index, source in enumerate(sources, start=1):
        published = source.published_at.isoformat() if source.published_at else "unknown date"
        content = [f"Passage [{p.passage_index}]: {clean(p.content)}" for p in by_source.get(source.id, [])]
        if not content and source.raw_content:
            content = [f"Content: {clean(source.raw_content[:1500])}"]
        lines = [
            f"Source [{index}]",
            f"Title: {clean(source.title)}",
            f"Publisher: {clean(source.publisher)}",
            f"Published: {published}",
        ]
        if include_url:
            lines.append(f"URL: {source.url}")
        blocks.append("\n".join(lines + content))
    return "\n\n".join(blocks)
