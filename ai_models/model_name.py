from enum import StrEnum

class ModelName(StrEnum):
    # https://cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/2-5-pro
    GeminiFlashLite = "gemini-2.0-flash-lite"
    GemimiPro = "gemini-2.5-pro-preview-05-06"
    GeminiFlash = "gemini-2.5-flash-preview-04-17"
    # With thinking mode
    Gemini25Flash = "gemini-2.5-flash-preview-05-20"
