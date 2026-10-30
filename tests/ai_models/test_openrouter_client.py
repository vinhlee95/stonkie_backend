from types import SimpleNamespace

from ai_models.model_name import ModelName
from ai_models.openrouter_client import OpenRouterClient


class _RecordingCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(())


class _FakeOpenAI:
    def __init__(self):
        self.completions = _RecordingCompletions()
        self.chat = self


def _client() -> tuple[OpenRouterClient, _RecordingCompletions]:
    client = OpenRouterClient(api_key="test-key", model_name=ModelName.Gpt4Mini)
    client.client = _FakeOpenAI()
    return client, client.client.completions


def test_stream_chat_keeps_the_8192_default_output_cap():
    client, completions = _client()

    list(client.stream_chat("p"))

    assert completions.calls[0]["max_tokens"] == 8192
    assert completions.calls[0]["model"] == "openai/gpt-4.1-mini"


def test_stream_chat_sends_a_requested_output_cap():
    client, completions = _client()

    list(client.stream_chat("p", max_tokens=32768))

    assert completions.calls[0]["max_tokens"] == 32768


class _Usage:
    def model_dump(self):
        return {
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "cost": 0.0002,
            "completion_tokens_details": {"reasoning_tokens": 25},
        }


def _event(*, content=None, extra=None, model="deepseek/deepseek-v4.1-flash-1017", usage=None, choices=True):
    delta = SimpleNamespace(content=content, model_extra=extra or {})
    return SimpleNamespace(model=model, usage=usage, choices=[SimpleNamespace(delta=delta)] if choices else [])


class _StreamingCompletions(_RecordingCompletions):
    def __init__(self, events):
        super().__init__()
        self.events = events

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self.events)


def _streaming_client(events) -> tuple[OpenRouterClient, _StreamingCompletions]:
    client = OpenRouterClient(api_key="test-key", model_name=ModelName.Auto)
    fake = _FakeOpenAI()
    fake.completions = _StreamingCompletions(events)
    client.client = fake
    return client, fake.completions


_REASONING_EVENTS = [
    _event(extra={"reasoning": "Gross margin 69.0 -> 67.2: ", "reasoning_details": [{"type": "reasoning.text"}]}),
    _event(extra={"reasoning_details": [{"type": "reasoning.text", "text": "falling."}]}),
    _event(extra={"reasoning_details": [{"type": "reasoning.encrypted", "data": "xyz"}]}),
    _event(content="Margins fell."),
    _event(usage=_Usage(), choices=False),
]


def test_stream_chat_without_reasoning_sends_no_extra_body_and_yields_no_reasoning():
    client, completions = _streaming_client(_REASONING_EVENTS)

    chunks = list(client.stream_chat("p"))

    assert chunks == ["Margins fell."]
    assert "extra_body" not in completions.calls[0]


def test_stream_chat_yields_reasoning_dicts_before_text_and_a_final_usage_dict():
    client, completions = _streaming_client(_REASONING_EVENTS)

    chunks = list(
        client.stream_chat(
            "p",
            reasoning={"effort": "low"},
            include_usage=True,
            auto_router_models=["deepseek/deepseek-v4.1-flash-1017"],
        )
    )

    assert chunks == [
        {"type": "reasoning", "text": "Gross margin 69.0 -> 67.2: "},
        {"type": "reasoning", "text": "falling."},
        "Margins fell.",
        {
            "type": "usage",
            "model": "deepseek/deepseek-v4.1-flash-1017",
            "cost": 0.0002,
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "reasoning_tokens": 25,
        },
    ]
    call = completions.calls[0]
    assert call["model"] == "openrouter/auto"
    assert call["extra_body"] == {
        "plugins": [{"id": "auto-router", "allowed_models": ["deepseek/deepseek-v4.1-flash-1017"]}],
        "reasoning": {"effort": "low"},
        "usage": {"include": True},
    }


def test_stream_chat_usage_without_a_usage_chunk_reports_the_requested_model():
    client, _ = _streaming_client([_event(content="x", model=None)])

    *_, usage = client.stream_chat("p", include_usage=True)

    assert usage["type"] == "usage"
    assert usage["model"] == "openrouter/auto"
    assert usage["cost"] is None
