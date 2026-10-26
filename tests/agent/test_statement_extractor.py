from agent.statement_extractor import extract_statement_periods
from ai_models.model_name import ModelName
from ai_models.openrouter_client import get_openrouter_model_name


class FakeAgent:
    def __init__(self, reply: str):
        self.reply = reply
        self.prompts = []

    def generate_text(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def test_parses_fenced_period_array():
    agent = FakeAgent('```json\n[{"period_end_year": "TTM", "metrics": {"Total Revenue": 1}}]\n```')

    assert extract_statement_periods("table html", agent=agent) == [
        {"period_end_year": "TTM", "metrics": {"Total Revenue": 1}}
    ]
    assert agent.prompts == ["table html"]


def test_unparseable_reply_returns_none():
    assert extract_statement_periods("table html", agent=FakeAgent('[{"period_end_year": 2025,')) is None


def test_extraction_model_routes_to_openai_gpt_41_mini():
    assert get_openrouter_model_name(ModelName.Gpt4Mini) == "openai/gpt-4.1-mini"
