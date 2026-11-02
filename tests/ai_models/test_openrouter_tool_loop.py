"""OpenRouterClient.stream_chat tool loop (fake OpenAI stream, no network)."""

import time
from types import SimpleNamespace

from ai_models.chat_tool import ChatTool, ChatToolResult
from ai_models.model_name import ModelName
from ai_models.openrouter_client import OpenRouterClient, _ToolMarkupFilter

QUERY = {"query": "Alphabet Q2 2026 other income"}


class _Usage:
    def __init__(self, cost):
        self.cost = cost

    def model_dump(self):
        return {"prompt_tokens": 10, "completion_tokens": 5, "cost": self.cost}


def _text(content, model="deepseek/deepseek-v4.1-flash-1017"):
    delta = SimpleNamespace(content=content, model_extra={}, tool_calls=None)
    return SimpleNamespace(model=model, usage=None, choices=[SimpleNamespace(delta=delta)])


def _tool_call(index, call_id, name, arguments):
    call = SimpleNamespace(index=index, id=call_id, function=SimpleNamespace(name=name, arguments=arguments))
    delta = SimpleNamespace(content=None, model_extra={}, tool_calls=[call])
    return SimpleNamespace(
        model="deepseek/deepseek-v4.1-flash-1017", usage=None, choices=[SimpleNamespace(delta=delta)]
    )


def _search_call(index=0, call_id="call_0", arguments='{"query": "Alphabet Q2 2026 other income"}'):
    return _tool_call(index, call_id, "web_search", arguments)


def _usage(cost):
    return SimpleNamespace(model="deepseek/deepseek-v4.1-flash-1017", usage=_Usage(cost), choices=[])


class _Completions:
    """Returns the next scripted round per create(); records every call."""

    def __init__(self, rounds, fail_first=False):
        self.rounds = list(rounds)
        self.calls = []
        self.fail_first = fail_first

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("No endpoints found that support tool use")
        return iter(self.rounds.pop(0))


def _client(rounds, fail_first=False):
    client = OpenRouterClient(api_key="test-key", model_name=ModelName.Auto)
    completions = _Completions(rounds, fail_first)
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def _tool(run=None, calls=None):
    def _run(arguments):
        if calls is not None:
            calls.append(arguments)
        return ChatToolResult("passages", {"source_count": 2})

    return ChatTool(
        name="web_search",
        description="Search the web",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        run=run or _run,
    )


def test_no_tools_keeps_current_request_shape():
    client, completions = _client([[_text("Answer.")]])

    chunks = list(client.stream_chat("p"))

    assert chunks == ["Answer."]
    assert "tools" not in completions.calls[0]


def test_tool_call_runs_tool_and_feeds_result_back():
    calls = []
    client, completions = _client([[_search_call()], [_text("Answer.")]])

    chunks = list(client.stream_chat("p", tools=[_tool(calls=calls)], max_tool_rounds=2))

    assert chunks == [
        {"type": "tool_call", "name": "web_search", "arguments": QUERY},
        {"type": "tool_result", "name": "web_search", "arguments": QUERY, "ok": True, "meta": {"source_count": 2}},
        "Answer.",
    ]
    assert calls == [QUERY]
    assert completions.calls[0]["tools"][0]["function"]["name"] == "web_search"
    assert completions.calls[1]["messages"][-1] == {"role": "tool", "tool_call_id": "call_0", "content": "passages"}
    assert completions.calls[1]["messages"][-2]["tool_calls"][0]["id"] == "call_0"


def test_tool_call_arguments_split_across_deltas_are_joined():
    client, _ = _client(
        [[_tool_call(0, "call_0", "web_search", '{"query": "Alpha'), _tool_call(0, None, None, 'bet"}')], [_text("A")]]
    )

    chunks = list(client.stream_chat("p", tools=[_tool()], max_tool_rounds=2))

    assert chunks[0] == {"type": "tool_call", "name": "web_search", "arguments": {"query": "Alphabet"}}


def test_preamble_before_tool_call_is_dropped():
    client, _ = _client([[_text("I'll look into it."), _search_call()], [_text("Answer.")]])

    chunks = list(client.stream_chat("p", tools=[_tool()], max_tool_rounds=2))

    assert "I'll look into it." not in chunks
    assert chunks[-1] == "Answer."


def test_long_text_without_tool_call_streams():
    text = "x" * 500
    client, completions = _client([[_text(text[:250]), _text(text[250:])]])

    chunks = list(client.stream_chat("p", tools=[_tool()], max_tool_rounds=2))

    assert "".join(c for c in chunks if isinstance(c, str)) == text
    assert len(completions.calls) == 1


def test_short_answer_without_tool_call_is_flushed_at_round_end():
    client, _ = _client([[_text("Short answer.")]])

    assert list(client.stream_chat("p", tools=[_tool()], max_tool_rounds=2)) == ["Short answer."]


def test_parallel_calls_capped_at_three():
    calls = []
    round_1 = [_search_call(i, f"call_{i}", f'{{"query": "q{i}"}}') for i in range(4)]
    client, _ = _client([round_1, [_text("A")]])

    list(client.stream_chat("p", tools=[_tool(calls=calls)], max_tool_rounds=2))

    assert sorted(c["query"] for c in calls) == ["q0", "q1", "q2"]


def test_parallel_calls_run_concurrently():
    def slow(arguments):
        time.sleep(0.3)
        return ChatToolResult("r", {})

    round_1 = [_search_call(i, f"call_{i}", f'{{"query": "q{i}"}}') for i in range(3)]
    client, _ = _client([round_1, [_text("A")]])

    start = time.perf_counter()
    list(client.stream_chat("p", tools=[_tool(run=slow)], max_tool_rounds=2))

    assert time.perf_counter() - start < 0.8


def test_round_cap_forces_final_answer_with_tool_choice_none():
    client, completions = _client([[_search_call(call_id="a")], [_search_call(call_id="b")], [_text("Final answer.")]])

    chunks = list(client.stream_chat("p", tools=[_tool()], max_tool_rounds=2))

    assert len(completions.calls) == 3
    # Tool history needs its tools declared; tool_choice "none" forces the answer (models given the history
    # without tools ignored the tool results in live checks).
    assert completions.calls[2]["tool_choice"] == "none"
    assert completions.calls[2]["tools"][0]["function"]["name"] == "web_search"
    exhausted = [i for i, c in enumerate(chunks) if c == {"type": "tool_budget_exhausted"}]
    last_result = max(i for i, c in enumerate(chunks) if isinstance(c, dict) and c.get("type") == "tool_result")
    assert len(exhausted) == 1 and exhausted[0] > last_result
    assert chunks[-1] == "Final answer."


def test_tool_exception_returns_search_failed():
    def boom(arguments):
        raise RuntimeError("brave down")

    client, completions = _client([[_search_call()], [_text("A")]])

    chunks = list(client.stream_chat("p", tools=[_tool(run=boom)], max_tool_rounds=2))

    assert completions.calls[1]["messages"][-1]["content"] == "search failed"
    assert {"type": "tool_result", "name": "web_search", "arguments": QUERY, "ok": False, "meta": {}} in chunks
    assert chunks[-1] == "A"


def test_bad_tool_arguments_return_search_failed():
    client, completions = _client([[_search_call(arguments="{not json")], [_text("A")]])

    chunks = list(client.stream_chat("p", tools=[_tool()], max_tool_rounds=2))

    assert completions.calls[1]["messages"][-1]["content"] == "search failed"
    assert chunks[-1] == "A"


def test_tools_request_error_falls_back_to_plain_stream():
    client, completions = _client([[_text("Plain answer.")]], fail_first=True)

    chunks = list(client.stream_chat("p", tools=[_tool()], max_tool_rounds=2))

    assert chunks == ["Plain answer."]
    assert "tools" not in completions.calls[1]


def test_usage_summed_across_rounds():
    client, _ = _client([[_search_call(), _usage(0.001)], [_text("A"), _usage(0.001)]])

    chunks = list(client.stream_chat("p", tools=[_tool()], max_tool_rounds=2, include_usage=True))

    usage = chunks[-1]
    assert usage["type"] == "usage"
    assert abs(usage["cost"] - 0.002) < 1e-9
    assert usage["prompt_tokens"] == 20


def test_hung_calls_share_one_round_timeout(monkeypatch):
    monkeypatch.setattr("ai_models.openrouter_client.TOOL_CALL_TIMEOUT_S", 0.2)

    def hung(arguments):
        time.sleep(1)
        return ChatToolResult("late", {})

    round_1 = [_search_call(i, f"call_{i}", f'{{"query": "q{i}"}}') for i in range(3)]
    client, completions = _client([round_1, [_text("A")]])

    start = time.perf_counter()
    chunks = list(client.stream_chat("p", tools=[_tool(run=hung)], max_tool_rounds=2))

    assert time.perf_counter() - start < 0.45
    assert [m["content"] for m in completions.calls[1]["messages"] if m["role"] == "tool"] == ["search failed"] * 3
    assert chunks[-1] == "A"


def test_later_round_error_falls_back_to_a_plain_answer():
    class _FailSecond(_Completions):
        def create(self, **kwargs):
            if len(self.calls) == 1:
                self.calls.append(kwargs)
                raise RuntimeError("400 bad tool history")
            return super().create(**kwargs)

    client = OpenRouterClient(api_key="test-key", model_name=ModelName.Auto)
    completions = _FailSecond([[_search_call()], [_text("Plain answer.")]])
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    chunks = list(client.stream_chat("p", tools=[_tool()], max_tool_rounds=2))

    assert chunks[-1] == "Plain answer."
    fallback = completions.calls[-1]
    assert "tools" not in fallback
    assert fallback["messages"] == [{"role": "user", "content": "p"}]


def test_preamble_flush_threshold_is_short():
    text = "x" * 100
    client, _ = _client([[_text(text), _text("y")]])

    chunks = list(client.stream_chat("p", tools=[_tool()], max_tool_rounds=2))

    assert chunks == [text, "y"]  # streamed past the threshold, not held to round end


def test_total_tool_calls_capped_per_stream(monkeypatch):
    monkeypatch.setattr("ai_models.openrouter_client.MAX_TOOL_CALLS_PER_STREAM", 4)
    calls = []
    rounds = [[_search_call(i, f"r{r}_{i}", f'{{"query": "r{r}q{i}"}}') for i in range(3)] for r in range(2)]
    client, completions = _client([*rounds, [_text("Final.")]])

    chunks = list(client.stream_chat("p", tools=[_tool(calls=calls)], max_tool_rounds=4))

    assert len(calls) == 4
    assert {"type": "tool_budget_exhausted"} in chunks
    assert completions.calls[-1]["tool_choice"] == "none"
    assert chunks[-1] == "Final."


def _answer(chunks):
    return "".join(c for c in chunks if isinstance(c, str))


def test_pseudo_tool_call_markup_in_text_is_stripped():
    # Seen live: DeepSeek wrote a tool call as answer text instead of a real tool_call.
    leaked = [
        "Nvidia leads.\n\n<inv",
        'oke>\n<parameter name="query">Nvidia AMD margins</parameter>\n</invoke>\n</in',
        "voke>\n\n**Profitability** Nvidia earns more.",
    ]
    client, _ = _client([[_text(t) for t in leaked]])

    answer = _answer(client.stream_chat("p", tools=[_tool()], max_tool_rounds=2))

    assert "invoke" not in answer and "parameter" not in answer
    assert answer.startswith("Nvidia leads.")
    assert answer.endswith("**Profitability** Nvidia earns more.")


def test_text_with_angle_brackets_is_kept():
    client, _ = _client([[_text("Margin <5% at AMD vs 75% at Nvidia; a <b>"), _text(" tag stays.")]])

    answer = _answer(client.stream_chat("p", tools=[_tool()], max_tool_rounds=2))

    assert answer == "Margin <5% at AMD vs 75% at Nvidia; a <b> tag stays."


def test_tool_call_after_a_streamed_preamble_still_runs():
    # A >80-char "I'll look into it" preamble streams before the tool call; the search must still run and an answer follow.
    preamble = "I'll look into what drove the jump in Alphabet's other income in Q2 2026 before answering."
    calls = []
    client, completions = _client([[_text(preamble), _search_call()], [_text("Answer.")]])

    chunks = list(client.stream_chat("p", tools=[_tool(calls=calls)], max_tool_rounds=2))

    assert calls == [QUERY]
    assert _answer(chunks) == preamble + "\n\nAnswer."
    assert completions.calls[1]["messages"][-1]["role"] == "tool"
    # The model is told what the user already saw, so it continues instead of restarting the answer.
    assert completions.calls[1]["messages"][-2]["content"] == preamble


def _filtered(*chunks):
    markup = _ToolMarkupFilter()
    return "".join(markup.feed(c) for c in chunks) + markup.flush()


def test_markup_filter_unclosed_invoke_resumes_at_the_next_prose_line():
    text = _filtered('Lead.\n<invoke>\n<parameter name="query">q</parameter>\n', "Nvidia earns more.\nEnd.")

    assert text == "Lead.\nNvidia earns more.\nEnd."


def test_markup_filter_unclosed_invoke_at_stream_end_keeps_trailing_prose():
    assert _filtered("Lead.\n<invoke>\n", "Tail prose") == "Lead.\nTail prose"


def test_markup_filter_stray_closing_tag_is_dropped():
    assert _filtered("A </invoke>B") == "A B"


def test_markup_filter_partial_tag_at_stream_end_is_kept():
    assert _filtered("Margin is <inv") == "Margin is <inv"


def test_markup_filter_drops_multiple_blocks():
    assert _filtered("A<invoke>x</invoke>B<invoke>y</invoke>C") == "ABC"
