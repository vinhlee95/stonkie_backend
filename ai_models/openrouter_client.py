import os
from typing import Dict, Iterable

from openai import OpenAI

from ai_models.model_name import ModelName

# OpenRouter-specific model name mappings
# OpenRouter requires 'google/' prefix for Gemini models
OPENROUTER_MODEL_MAP: Dict[str, str] = {
    # Map generic Gemini names to OpenRouter format
    "gemini-2.5-flash-lite": "google/gemini-2.5-flash-lite",
    "gemini-2.5-flash": "google/gemini-2.5-flash",
    # OpenAI models pass through unchanged
    "gpt-4.1-mini": "gpt-4.1-mini",
    "text-embedding-3-small": "text-embedding-3-small",
}


def get_openrouter_model_name(model_name: str) -> str:
    """Convert generic model name to OpenRouter-specific format.

    Args:
        model_name: Generic model name (e.g., 'gemini-2.5-flash-lite')

    Returns:
        OpenRouter-specific model name (e.g., 'google/gemini-2.5-flash-lite')
        Falls back to original name if no mapping exists.
    """
    return OPENROUTER_MODEL_MAP.get(model_name, model_name)


class OpenRouterClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, model_name: str | None = None):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is required to use OpenRouterClient")

        self.base_url = base_url or os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        # Convert generic model name to OpenRouter-specific format
        generic_name = model_name or ModelName.Gemini25Flash
        self.model_name = get_openrouter_model_name(generic_name)
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def stream_chat(self, prompt: str, use_google_search: bool = False) -> Iterable[str]:
        """
        Stream chat completions as plain text chunks.

        Args:
            prompt: The user prompt
            model: Model name (defaults to self.default_model)
            use_google_search: If True, appends ':online' to model name to enable web search
        """
        # model_name is already in OpenRouter format from __init__
        chosen_model = self.model_name

        # OpenRouter enables web search by appending ':online' to the model name
        if use_google_search and not chosen_model.endswith(":online"):
            chosen_model = f"{chosen_model}:online"

        response = self.client.chat.completions.create(
            model=chosen_model,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        )

        for event in response:
            delta = event.choices[0].delta.content
            if delta:
                yield delta
