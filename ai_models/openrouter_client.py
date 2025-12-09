import os
from typing import Iterable

from openai import OpenAI

# Minimal streaming client for OpenRouter chat completions.


class OpenRouterClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, default_model: str | None = None):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is required to use OpenRouterClient")

        self.base_url = base_url or os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        self.default_model = default_model or os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def stream_chat(self, prompt: str, model: str | None = None, use_google_search: bool = False) -> Iterable[str]:
        """
        Stream chat completions as plain text chunks.

        Args:
            prompt: The user prompt
            model: Model name (defaults to self.default_model)
            use_google_search: If True, appends ':online' to model name to enable web search
        """
        chosen_model = model or self.default_model

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
