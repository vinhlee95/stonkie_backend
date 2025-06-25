from enum import StrEnum

class ModelName(StrEnum):
    # https://cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/2-5-pro
    GeminiFlashLite = "gemini-2.0-flash-lite"
    GemimiPro = "gemini-2.5-pro-preview-05-06"
    Gemini25FlashLite = "gemini-2.5-flash-lite-preview-06-17"
    GeminiFlash = "gemini-2.5-flash"