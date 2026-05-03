from datetime import UTC, datetime

from services.analyze_retrieval.schemas import AnalyzeSource


def _source(idx: int) -> AnalyzeSource:
    return AnalyzeSource(
        id=f"s_{idx}",
        url=f"https://example.com/{idx}",
        title=f"Title {idx}",
        publisher=f"Publisher {idx}",
        published_at=datetime(2026, 4, idx, 12, 0, tzinfo=UTC),
        is_trusted=True,
    )


def test_build_sources_event_emits_all_retrieved_sources_in_order() -> None:
    from services.analyze_retrieval.citation_index import build_sources_event

    event = build_sources_event([_source(1), _source(2)])

    assert event == {
        "type": "sources",
        "body": [
            {
                "source_id": "s_1",
                "url": "https://example.com/1",
                "title": "Title 1",
                "publisher": "Publisher 1",
                "published_at": "2026-04-01T12:00:00Z",
                "is_trusted": True,
            },
            {
                "source_id": "s_2",
                "url": "https://example.com/2",
                "title": "Title 2",
                "publisher": "Publisher 2",
                "published_at": "2026-04-02T12:00:00Z",
                "is_trusted": True,
            },
        ],
    }


def test_build_sources_event_returns_empty_when_no_sources_retrieved() -> None:
    from services.analyze_retrieval.citation_index import build_sources_event

    event = build_sources_event([])
    assert event == {"type": "sources", "body": []}
