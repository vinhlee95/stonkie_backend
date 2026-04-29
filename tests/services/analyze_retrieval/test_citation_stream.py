import json
import logging
from datetime import UTC, datetime

import pytest

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


def test_build_sources_event_maps_single_citation_to_first_source() -> None:
    from services.analyze_retrieval.citation_index import build_sources_event

    event = build_sources_event("Answer with citation [1].", [_source(1), _source(2)])

    assert event == {
        "type": "sources",
        "body": {
            "sources": [
                {
                    "source_id": "s_1",
                    "url": "https://example.com/1",
                    "title": "Title 1",
                    "publisher": "Publisher 1",
                    "published_at": "2026-04-01T12:00:00Z",
                    "is_trusted": True,
                }
            ]
        },
    }


def test_build_sources_event_dedupes_repeated_citations_in_first_seen_order() -> None:
    from services.analyze_retrieval.citation_index import build_sources_event

    event = build_sources_event("Answer [1][2][1] done.", [_source(1), _source(2), _source(3)])

    assert [item["source_id"] for item in event["body"]["sources"]] == ["s_1", "s_2"]


def test_build_sources_event_drops_out_of_range_citations_and_logs_counter(caplog: pytest.LogCaptureFixture) -> None:
    from services.analyze_retrieval.citation_index import build_sources_event

    caplog.set_level(logging.INFO, logger="app.analyze_retrieval")
    event = build_sources_event("Bad citation [9], valid citation [1].", [_source(1), _source(2)])

    assert [item["source_id"] for item in event["body"]["sources"]] == ["s_1"]
    payload = json.loads(caplog.records[-1].message)
    assert payload["out_of_range_citation_count"] == 1


def test_build_sources_event_returns_empty_sources_when_no_citations() -> None:
    from services.analyze_retrieval.citation_index import build_sources_event

    event = build_sources_event("No citations here.", [_source(1), _source(2)])
    assert event == {"type": "sources", "body": {"sources": []}}


def test_build_sources_event_logs_uncited_source_count(caplog: pytest.LogCaptureFixture) -> None:
    from services.analyze_retrieval.citation_index import build_sources_event

    caplog.set_level(logging.INFO, logger="app.analyze_retrieval")
    event = build_sources_event("Use [2] only.", [_source(1), _source(2), _source(3)])

    assert [item["source_id"] for item in event["body"]["sources"]] == ["s_2"]
    payload = json.loads(caplog.records[-1].message)
    assert payload["uncited_source_count"] == 2
