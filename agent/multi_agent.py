from typing import Iterable

from ai_models.openrouter_client import OpenRouterClient


class MultiAgent:
    """Wrapper class for OpenRouter client to provide consistent interface across codebase"""

    def __init__(self, model_name: str | None = None):
        """
        Initialize the OpenRouter client wrapper

        Args:
            model_name: The model to use (defaults to OpenRouterClient's default)
            api_key: OpenRouter API key (defaults to env var OPENROUTER_API_KEY)
            base_url: OpenRouter base URL (defaults to env var or https://openrouter.ai/api/v1)
        """
        self.client = OpenRouterClient(model_name=model_name)

    @property
    def model_name(self) -> str:
        """Get the current model name"""
        return self.client.model_name

    def generate_content(
        self,
        prompt: str,
        use_google_search: bool = False,
    ) -> Iterable[str]:
        """
        Generate content using OpenRouter

        Args:
            prompt: The user prompt
            model_name: Model name to use (defaults to client's default model)
            use_google_search: If True, enables web search by appending ':online' to model name

        Returns:
            Iterable of string chunks when streaming
        """
        return self.client.stream_chat(prompt=prompt, use_google_search=use_google_search)
