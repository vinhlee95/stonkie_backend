"""thinking_status events for the answer model's web_search tool rounds (spec section 5 status catalogue)."""

from __future__ import annotations

from ai_models.model_name import ModelName
from services.analysis_progress import AnalysisPhase, ThinkingProgress
from services.question_analyzer.handlers_v2 import (
    CHECKING_STATUS,
    CONTEXT_STATUS,
    REASONING_STATUS,
    answer_call,
    answer_events,
    tool_status_event,
)


def _call(query):
    return {"type": "tool_call", "name": "web_search", "arguments": {"query": query}}


def _result(query, ok=True, count=0, publishers=()):
    meta = {"source_count": count, "publishers": list(publishers)} if ok else {}
    return {"type": "tool_result", "name": "web_search", "arguments": {"query": query}, "ok": ok, "meta": meta}


def _writing(progress):
    return lambda: progress.status("Writing your answer...", phase=AnalysisPhase.ANALYZE)


def _statuses(events):
    return [e["body"] for e in events if e["type"] == "thinking_status"]


_TOOLS_OPTIONS = {"include_usage": True, "tools": ["web_search"], "max_tool_rounds": 2}


def test_tools_on_status_sequence():
    progress = ThinkingProgress()
    stream = [
        _call("q1"),
        _result("q1", count=2, publishers=["Reuters"]),
        _call("q2"),
        _result("q2", ok=False),
        "A",
    ]

    events = list(answer_events(iter(stream), _TOOLS_OPTIONS, progress, _writing(progress)))

    assert _statuses(events) == [
        "Checking what's behind the numbers...",
        'Digging deeper: "q1"...',
        'Found 2 sources on "q1": Reuters',
        'Digging deeper: "q2"...',
        'Nothing new on "q2", continuing...',
        "Writing your answer...",
    ]
    assert [e["step"] for e in events if e["type"] == "thinking_status"] == [1, 2, 3, 4, 5, 6]
    assert events[-1] == {"type": "answer", "body": "A"}


def test_tool_statuses_use_search_phase():
    progress = ThinkingProgress()

    events = list(answer_events(iter([_call("q"), "A"]), _TOOLS_OPTIONS, progress, _writing(progress)))

    assert events[0]["phase"] == AnalysisPhase.ANALYZE
    assert events[1]["phase"] == AnalysisPhase.SEARCH


def test_tool_result_zero_sources_says_nothing_new():
    assert tool_status_event(ThinkingProgress(), _result("q", count=0))["body"] == 'Nothing new on "q", continuing...'


def test_found_one_source_without_trusted_publishers():
    assert tool_status_event(ThinkingProgress(), _result("q", count=1))["body"] == 'Found 1 source on "q"'


def test_found_lists_at_most_three_publishers():
    event = tool_status_event(ThinkingProgress(), _result("q", count=5, publishers=["A", "B", "C", "D"]))

    assert event["body"] == 'Found 5 sources on "q": A, B, C'


def test_budget_exhausted_status():
    event = tool_status_event(ThinkingProgress(), {"type": "tool_budget_exhausted"})

    assert event["body"] == "Search limit reached, writing with what I found..."
    assert event["phase"] == AnalysisPhase.ANALYZE


def test_long_query_trimmed_to_80_chars():
    query = "x" * 100

    event = tool_status_event(ThinkingProgress(), _call(query))

    assert event["body"] == f'Digging deeper: "{"x" * 80}…"...'


def test_unknown_dict_has_no_status():
    assert tool_status_event(ThinkingProgress(), {"type": "url_citation"}) is None


def test_reasoning_on_with_tools_uses_reasoning_status_first():
    progress = ThinkingProgress()
    options = {**_TOOLS_OPTIONS, "reasoning": {"effort": "low"}}

    events = list(answer_events(iter([_call("q"), "A"]), options, progress, _writing(progress)))

    assert _statuses(events)[:2] == [REASONING_STATUS, 'Digging deeper: "q"...']
    assert CHECKING_STATUS not in _statuses(events)


def test_no_status_after_answer_started():
    progress = ThinkingProgress()

    events = list(answer_events(iter(["A", _call("late")]), _TOOLS_OPTIONS, progress, _writing(progress)))

    assert 'Digging deeper: "late"...' not in _statuses(events)


def test_writing_status_is_built_after_the_tool_rounds():
    progress = ThinkingProgress()
    built_after = []
    stream_state = {"tool_done": False}

    def stream():
        yield _call("q")
        stream_state["tool_done"] = True
        yield "A"

    def writing():
        built_after.append(stream_state["tool_done"])
        return progress.status("Writing your answer...", phase=AnalysisPhase.ANALYZE)

    list(answer_events(stream(), _TOOLS_OPTIONS, progress, writing))

    assert built_after == [True]


def test_answer_call_adds_tools():
    tools = [object()]

    model, options = answer_call(ModelName.Auto, False, tools=tools, max_tool_rounds=4)

    assert model == ModelName.Auto
    assert options == {"include_usage": True, "tools": tools, "max_tool_rounds": 4}


def test_answer_call_without_tools_is_unchanged():
    assert answer_call(ModelName.Auto, False) == (ModelName.Auto, {"include_usage": True})


def test_holding_status_can_be_overridden_for_non_numeric_questions():
    progress = ThinkingProgress()

    events = list(
        answer_events(iter(["A"]), _TOOLS_OPTIONS, progress, _writing(progress), holding_status=CONTEXT_STATUS)
    )

    assert _statuses(events)[0] == "Checking for more context..."
