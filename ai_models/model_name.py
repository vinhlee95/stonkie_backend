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
    Gemini25Pro = "gemini-2.5-pro"
    Gemini25FlashExp0827 = "gemini-2.5-flash-exp-0827"  # Used in scripts
    Gemini20FlashThinkingExp = "gemini-2.0-flash-thinking-exp-01-21"  # Preview model
    Gemini20Flash001 = "gemini-2.0-flash-001"  # Older version

    # OpenAI Models (Native OpenAI format)
    Gpt4Mini = "gpt-4.1-mini"
    TextEmbeddingSmall = "text-embedding-3-small"
    TextEmbeddingLarge = "text-embedding-3-large"

    # Backward compatibility aliases (exact names from original enum)
    GeminiFlash = "gemini-2.5-flash"
    GeminiPro = "gemini-2.5-pro"  # Fixed typo from GemimiPro
