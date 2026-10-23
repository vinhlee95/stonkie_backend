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
