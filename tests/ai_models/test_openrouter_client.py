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
