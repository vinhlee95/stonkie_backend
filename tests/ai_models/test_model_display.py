import pytest

from ai_models.model_display import display_model_name, for_client


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("deepseek/deepseek-v4.1-flash-1017", "DeepSeek 4.1"),
        ("google/gemini-3.5-flash", "Gemini 3.5"),
        ("google/gemini-3.1-flash-lite:nitro", "Gemini 3.1"),
        ("anthropic/claude-sonnet-4.6", "Claude Sonnet 4.6"),
        ("qwen/qwen3.7-flash", "Qwen 3.7"),
        ("z-ai/glm-5.3-flash", "GLM 5.3"),
        ("openai/gpt-4.1-mini", "GPT 4.1"),
        ("openai/gpt-5", "GPT 5"),
        ("meta-llama/llama-4-maverick", "Llama 4"),
        ("mistralai/mistral-large-2411", "Mistral Large"),  # 2411 is a release date, not a version
        ("moonshotai/kimi-k2", "Kimi K2"),  # no version: the family words
        ("openrouter/auto", "Auto"),
    ],
)
def test_display_model_name_is_family_and_version_without_v(model_id, expected):
    assert display_model_name(model_id) == expected


@pytest.mark.parametrize("model_id", ["", "unknown"])
def test_display_model_name_leaves_placeholders_alone(model_id):
    assert display_model_name(model_id) == model_id


def test_for_client_rewrites_only_model_used():
    assert for_client({"type": "model_used", "body": "google/gemini-3.5-flash"}) == {
        "type": "model_used",
        "body": "Gemini 3.5",
    }
    answer = {"type": "answer", "body": "google/gemini-3.5-flash"}
    assert for_client(answer) is answer
