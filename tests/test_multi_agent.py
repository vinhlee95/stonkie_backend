from unittest.mock import patch

from agent.multi_agent import MultiAgent


class _FakeClient:
    model_name = "fake-model"

    def __init__(self, chunks):
        self._chunks = chunks

    def stream_chat(self, prompt, use_google_search=False):
        yield from self._chunks


def _agent(chunks) -> MultiAgent:
    with patch("agent.multi_agent.OpenRouterClient", return_value=_FakeClient(chunks)):
        return MultiAgent()


def test_generate_text_joins_text_and_drops_citations():
    agent = _agent(["Alpha", {"type": "url_citation", "url": "https://x"}, " beta"])
    assert agent.generate_text("p") == "Alpha beta"


def test_generate_text_empty_stream():
    assert _agent([]).generate_text("p") == ""


def test_generate_text_passes_an_output_cap_only_when_given():
    calls = []

    class _CapClient(_FakeClient):
        def stream_chat(self, prompt, use_google_search=False, **kwargs):
            calls.append(kwargs)
            yield from self._chunks

    with patch("agent.multi_agent.OpenRouterClient", return_value=_CapClient(["x"])):
        agent = MultiAgent()

    agent.generate_text("p")
    agent.generate_text("p", max_tokens=32768)

    assert calls == [{}, {"max_tokens": 32768}]
