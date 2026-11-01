"""Human-readable model names for clients: "deepseek/deepseek-v4.1-flash-1017" -> "DeepSeek 4.1".

Only what clients see changes; streams, logs, Langfuse and the semantic cache keep the OpenRouter id.
"""

from __future__ import annotations

import re

# Words whose casing `str.capitalize` gets wrong.
_WORDS = {"deepseek": "DeepSeek", "gpt": "GPT", "glm": "GLM", "minimax": "MiniMax"}
# "4.1", "v4.1", "5"; four-plus digits without a dot are release dates ("1017", "2411"), not versions.
_VERSION = re.compile(r"v?(\d+(?:\.\d+)*)")
# Family and version in one token: "qwen3.7".
_GLUED = re.compile(r"([a-z]{2,})(\d+(?:\.\d+)*)")


def _version(token: str) -> str | None:
    match = _VERSION.fullmatch(token)
    if not match or ("." not in match[1] and len(match[1]) >= 4):
        return None
    return match[1]


def _word(token: str) -> str:
    return _WORDS.get(token, token.capitalize())


def display_model_name(model_id: str) -> str:
    """Family and version, no "v" and no variant ("flash", "mini", ":nitro"): "Gemini 3.5", "Claude Sonnet 4.6".

    Without a version, the family words ("Mistral Large"). Empty and "unknown" pass through.
    """
    if model_id in ("", "unknown"):
        return model_id
    slug = model_id.split("/")[-1].split(":")[0].lower()
    if slug == "auto":
        return "Auto"
    words: list[str] = []
    for token in slug.split("-"):
        version = _version(token)
        if version and words:
            return f"{' '.join(words)} {version}"
        glued = _GLUED.fullmatch(token)
        if glued and not words:
            return f"{_word(glued[1])} {glued[2]}"
        if not token.isdigit():
            words.append(_word(token))
    return " ".join(words) or model_id


def for_client(event: dict) -> dict:
    """An SSE event as sent to clients: `model_used` carries the display name."""
    if event.get("type") == "model_used" and isinstance(event.get("body"), str):
        return {**event, "body": display_model_name(event["body"])}
    return event
