from typing import Any, Literal

from ai_models.openai import OpenAIModel

SupportedModel = Literal["openai"]


class Agent:
    """Wrapper around the OpenAI model (embeddings + structured extraction).
    Chat/LLM generation goes through MultiAgent (OpenRouter)."""

    def __init__(self, model_type: SupportedModel = "openai", model_name: str | None = None):
        """
        Initialize the AI model wrapper

        Args:
            model_type (str): Type of AI model to use ("openai")
        """
        self.model_type = model_type
        if model_type == "openai":
            self.model = OpenAIModel(model_name=model_name)
        else:
            raise ValueError(f"Unsupported model type: {model_type}")

    def generate_content(
        self,
        prompt: str | list[str],
        model_name: str | None = None,
        stream=True,
        thought: bool = False,
        use_google_search: bool = False,
        use_url_context: bool = False,
        **kwargs,
    ) -> Any:
        """
        Generate content using the configured AI model
        """
        return self.model.generate_content(
            prompt,
            model_name=model_name,
            stream=stream,
            thought=thought,
            use_google_search=use_google_search,
            use_url_context=use_url_context,
            **kwargs,
        )

    def generate_embedding(self, input: str, model: str = "text-embedding-3-small"):
        return self.model.generate_embedding(input=input, model=model)
