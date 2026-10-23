import pytest

from services.revenue_insight import process_streaming_insights


async def _collect(chunks):
    return [event async for event in process_streaming_insights(iter(chunks))]


@pytest.mark.asyncio
async def test_splits_insights_across_chunks_and_skips_citation_dicts():
    events = await _collect(
        [
            "---INSIGHT_START---First ",
            {"type": "url_citation", "url": "https://x"},
            "insight---INSIGHT_END------INSIGHT_START---Second---INSIGHT_END---",
            "---COMPLETE---",
        ]
    )

    successes = [e["data"]["content"] for e in events if e["type"] == "success"]
    assert successes == ["First insight", "Second"]
    assert all(isinstance(e, dict) and "url_citation" not in str(e) for e in events)


@pytest.mark.asyncio
async def test_stops_at_complete_marker():
    events = await _collect(["---COMPLETE---", "---INSIGHT_START---late---INSIGHT_END---"])
    assert [e for e in events if e["type"] == "success"] == []
