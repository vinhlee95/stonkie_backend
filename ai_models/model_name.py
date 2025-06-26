from enum import StrEnum

class ModelName(StrEnum):
    # https://cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/2-5-pro
    Gemini25FlashLite = "gemini-2.5-flash-lite-preview-06-17"
    GeminiFlash = "gemini-2.5-flash"
    GemimiPro = "gemini-2.5-pro"