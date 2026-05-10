from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.analyze_retrieval.schemas import AnalyzeRetrievalResult, AnalyzeSource
from services.recap_analyze import RecapAnalyzeStreamService


def _recap():
    return SimpleNamespace(
        id=91,
        market="US",
        cadence="weekly",
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        summary="US stocks rose as tech earnings offset fading rate-cut expectations.",
        bullets=[
            {
                "text": "Megacap tech led the market higher.",
                "citations": [{"source_id": "src-1"}],
            },
            {
                "text": "Rate-cut expectations faded after sticky inflation.",
                "citations": [{"source_id": "src-2"}],
            },
        ],
        sources=[
            {
                "id": "src-1",
                "url": "https://www.reuters.com/markets/us/tech",
                "title": "Tech leads gains",
                "publisher": "reuters.com",
                "published_at": "2026-04-24T12:00:00Z",
            },
            {
                "id": "src-2",
                "url": "https://www.wsj.com/markets/rates",
                "title": "Rate bets fade",
                "publisher": "wsj.com",
                "published_at": "2026-04-24T13:00:00Z",
            },
        ],
    )


async def _connected():
    return False


async def _collect(service, **overrides):
    kwargs = {
        "recap": _recap(),
        "question": "What drove the rally?",
        "preferred_model": "fastest",
        "conversation_id": "conv-1",
        "anon_user_id": "anon-1",
        "is_disconnected": _connected,
        "debug_prompt_context": False,
    }
    kwargs.update(overrides)
    events = []
    async for event in service.stream(**kwargs):
        events.append(event)
    return events


class FakeAgent:
    prompts: list[str] = []
    gate_route = "recap_related"
    answer = "Recap-grounded answer."

    def __init__(self, model_name):
        self.model_name = model_name

    def generate_content(self, *, prompt: str, use_google_search: bool):
        self.prompts.append(prompt)
        if "strict JSON classifier" in prompt:
            yield f'{{"route":"{self.gate_route}","reason":"test"}}'
            return
        yield self.answer


@pytest.fixture(autouse=True)
def _conversation_store_patches():
    with (
        patch("services.recap_analyze.get_conversation_history_for_prompt", return_value=[]),
        patch("services.recap_analyze.append_user_message"),
        patch("services.recap_analyze.append_assistant_message"),
    ):
        yield


@pytest.mark.asyncio
async def test_recap_analyze_service_answers_recap_related_question_from_recap_sources():
    FakeAgent.prompts = []
    FakeAgent.gate_route = "recap_related"
    FakeAgent.answer = "Tech earnings offset rate worries in the recap."

    with patch("services.recap_analyze.MultiAgent", FakeAgent):
        events = await _collect(RecapAnalyzeStreamService())

    event_types = [event["type"] for event in events]
    assert event_types[0] == "conversation"
    assert "answer" in event_types
    assert "sources" in event_types
    assert "related_question" not in event_types
    sources = next(event["body"] for event in events if event["type"] == "sources")
    assert [source["source_id"] for source in sources] == ["src-1", "src-2"]
    answer_prompt = FakeAgent.prompts[-1]
    assert "US stocks rose as tech earnings" in answer_prompt
    assert "Megacap tech led the market higher" in answer_prompt
    assert "You are a financial analyst" in answer_prompt
    assert "Keep the answer under 150 words" in answer_prompt
    assert "Use short paragraphs or up to 4 bullets" in answer_prompt


@pytest.mark.asyncio
async def test_recap_analyze_service_searches_with_recap_aware_query_for_market_questions():
    FakeAgent.prompts = []
    FakeAgent.gate_route = "recap_related"
    FakeAgent.answer = "Fresh context still points back to the recap."
    captured = {}

    def fake_retrieve_for_analyze(**kwargs):
        captured.update(kwargs)
        source = AnalyzeSource(
            id="brave-1",
            url="https://www.reuters.com/markets/latest",
            title="Latest market update",
            publisher="reuters.com",
            published_at=datetime.fromisoformat("2026-04-24T12:00:00+00:00"),
            is_trusted=True,
            raw_content="Stocks moved after the recap as yields rose.",
        )
        stale_source = AnalyzeSource(
            id="stale-1",
            url="https://www.reuters.com/markets/stale",
            title="Stale market update",
            publisher="reuters.com",
            published_at=datetime.fromisoformat("2026-04-19T12:00:00+00:00"),
            is_trusted=True,
            raw_content="This is before the recap period.",
        )
        return AnalyzeRetrievalResult(
            sources=[stale_source, source],
            selected_passages=[],
            query=kwargs["question"],
            market=kwargs["market"],
            request_id=kwargs["request_id"],
        )

    with (
        patch("services.recap_analyze.MultiAgent", FakeAgent),
        patch("services.recap_analyze.retrieve_for_analyze", side_effect=fake_retrieve_for_analyze),
    ):
        events = await _collect(
            RecapAnalyzeStreamService(),
            question="What changed after this recap?",
        )

    assert captured["market"] == "GLOBAL"
    assert "What changed after this recap?" in captured["question"]
    assert "after April 24 2026 latest" in captured["question"]
    assert "US weekly after April 24 2026 latest recap period 2026-04-20 2026-04-24" in captured["question"]
    assert "stocks rose tech earnings offset fading" in captured["question"]
    assert len(captured["question"]) <= 240
    sources = next(event["body"] for event in events if event["type"] == "sources")
    assert [source["source_id"] for source in sources] == ["brave-1"]
    thinking_bodies = [event["body"] for event in events if event["type"] == "thinking_status"]
    assert "Reading 1 sources: reuters.com" in thinking_bodies
    assert "External search context" in FakeAgent.prompts[-1]
    assert "explicitly connect the answer back to the recap" in FakeAgent.prompts[-1]


@pytest.mark.asyncio
async def test_recap_analyze_service_redirects_unrelated_nonfinance_without_search():
    FakeAgent.prompts = []
    FakeAgent.gate_route = "unrelated_nonfinance"

    with (
        patch("services.recap_analyze.MultiAgent", FakeAgent),
        patch("services.recap_analyze.retrieve_for_analyze") as retrieve,
    ):
        events = await _collect(
            RecapAnalyzeStreamService(),
            question="Write me a pasta recipe",
        )

    retrieve.assert_not_called()
    answers = [event["body"] for event in events if event["type"] == "answer"]
    assert answers == [
        "This chat is focused on the selected market recap and related market questions. "
        "Ask me about the recap, the market moves, sectors, companies, macro drivers, or what changed since the recap."
    ]
