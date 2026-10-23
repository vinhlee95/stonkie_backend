import pytest
from pydantic import ValidationError

import services.company_insight as company_insight
from services.company_insight import CompanyInsight

ARRAY = '[{"title": "A", "content": "x"}, {"title": "B", "content": "y [z]"}]'


class _FakeAgent:
    def __init__(self, text: str):
        self.text = text

    def generate_text(self, prompt: str) -> str:
        return self.text


@pytest.fixture
def use_reply(monkeypatch):
    def _use(text: str):
        monkeypatch.setattr(company_insight, "agent", _FakeAgent(text))

    return _use


@pytest.mark.parametrize(
    "reply",
    [ARRAY, f"```json\n{ARRAY}\n```", f"Here are the insights:\n{ARRAY}\nHope that helps."],
)
def test_parses_array_from_plain_fenced_or_prose_reply(use_reply, reply):
    use_reply(reply)
    assert company_insight._generate_insights("p") == [
        CompanyInsight(title="A", content="x"),
        CompanyInsight(title="B", content="y [z]"),
    ]


def test_empty_array(use_reply):
    use_reply("[]")
    assert company_insight._generate_insights("p") == []


def test_no_array_raises(use_reply):
    use_reply("Sorry, I cannot help with that.")
    with pytest.raises(ValueError):
        company_insight._generate_insights("p")


def test_item_missing_field_raises(use_reply):
    use_reply('[{"title": "A"}]')
    with pytest.raises(ValidationError):
        company_insight._generate_insights("p")
