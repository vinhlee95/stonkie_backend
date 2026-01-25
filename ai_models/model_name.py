from enum import StrEnum


class ModelName(StrEnum):
    """Provider-agnostic model names for use across the application.

    Services should use these generic names (e.g., ModelName.Gemini25FlashLite).
    Provider-specific clients (OpenRouter, Gemini, OpenAI) handle mapping to their
    specific naming conventions internally.
    """

    # Gemini Models (Generic format - provider clients will map to their specific format)
    # https://cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/2-5-pro
    Gemini25FlashLite = "gemini-2.5-flash-lite"
    Gemini25Flash = "gemini-2.5-flash"
    Gemini30Flash = "gemini-3-flash-preview"
    Gemini30Pro = "gemini-3-pro-preview"

    # OpenRouter Auto Router (for "best" mode)
    # https://openrouter.ai/docs/guides/routing/auto-model-selection
    Auto = "auto"
    Fastest = "fastest"

    # OpenAI Models (Native OpenAI format)
    Gpt4Mini = "gpt-4.1-mini"
    TextEmbeddingSmall = "text-embedding-3-small"
